from keras.models import Model
from keras.layers import Input, Dense, LSTM, Activation, Masking, Reshape
from keras.layers import multiply, concatenate
from keras.layers import Conv1D, BatchNormalization, GlobalAveragePooling1D
from keras.layers import Permute, Dropout

from utils.constants import MAX_NB_VARIABLES, NB_CLASSES_LIST, MAX_TIMESTEPS_LIST
from utils.keras_utils import train_model, evaluate_model, set_trainable
from utils.layer_utils import AttentionLSTM

from sklearn.externals import joblib

import warnings
warnings.simplefilter('ignore', category=DeprecationWarning)

def Conv1D_Stack(input_stack, conv1d_depth, conv1d_kernel, 
				activation_func, local_initializer):
	
	output_stack = Conv1D(conv1d_depth, conv1d_kernel,
						kernel_initializer=local_initializer)(input_stack)

	output_stack = BatchNormalization()(output_stack)
	
	output_stack = Activation(activation_func)(output_stack)

	return output_stack

def squeeze_excite_block(tower, 
						 squeeze_ratio = 16, 
						 activation_func = 'relu',
						 logit_output = 'sigmoid', 
						 kernel_initializer = 'he_normal', 
						 use_bias = False):
    ''' Create a squeeze-excite block
    Args:
        tower: tower tensor
        filters: number of output filters
        k: width factor
	
    Returns: a keras tensor
    '''
    filters = tower._keras_shape[-1] # channel_axis = -1 for TF

    squeeze_excite = GlobalAveragePooling1D()(tower)
    squeeze_excite = Reshape((1, filters))(squeeze_excite)

    squeeze_excite = Dense(filters // squeeze_ratio, 
    					activation=activation_func,
    					kernel_initializer=kernel_initializer, 
    					use_bias=use_bias)(squeeze_excite)
    squeeze_excite = Dense(filters, activation=logit_output, 
    			kernel_initializer=kernel_initializer, 
    			use_bias=use_bias)(squeeze_excite)
    
    squeeze_excite = multiply([tower, squeeze_excite])
    
    return squeeze_excite

class MLSTM_FCN(object):
	def __init__(self, DATASET_INDEX=None, TRAINABLE=True):

		num_max_times = MAX_TIMESTEPS_LIST[DATASET_INDEX]
		num_max_var = MAX_NB_VARIABLES[DATASET_INDEX]
		
		self.num_classes = NB_CLASSES_LIST[DATASET_INDEX]

		self.input_shape = (num_max_var, num_max_times)

	def create_model(self, 
					 n_lstm_cells = 8, 
					 dropout_rate = 0.8, 
					 permute_dims = (2,1), 
					 conv1d_depths = [128, 256, 128], 
					 conv1d_kernels = [8, 5, 3], 
					 local_initializer = 'he_uniform', 
					 activation_func = 'relu', 
					 Attention = False, 
 					 Squeeze = True, 
					 squeeze_ratio = 16, 
					 pre_convolve_rnn = False, 
					 pre_convolve_rnn_stride = 2, 
 					 squeeze_initializer = 'he_normal', 
					 logit_output = 'sigmoid', 
					 use_bias = False,
					 verbose = False):

		input_layer = Input(shape=self.input_shape)

		''' Create the LSTM-RNN Tower for the MLSTM-FCN Architecture '''
		if pre_convolve_rnn:
			''' sabsample timesteps to prevent "Out-of-Memory Errors" due to the Attention LSTM's size '''

			# permute to match Conv1D configuration
		    rnn_tower = Permute(permute_dims)(input_layer)
	    	rnn_tower = Conv1D(self.input_shape[0] // stride, conv1d_kernels[0], 
	    						strides=stride, padding='same', 
	    						activation=activation_func, use_bias=use_bias,
	               				kernel_initializer=local_initializer)(rnn_tower)

	        # re-permute to match LSTM configuration
	        rnn_tower = Permute(permute_dims)(rnn_tower)
			
			rnn_tower = Masking()(rnn_tower)
		else:
			# Default behaviour is to mask the input layer itself
			rnn_tower = Masking()(input_layer)

		if Attention:
			rnn_tower = AttentionLSTM(n_lstm_cells)(rnn_tower)
		else:
			rnn_tower = LSTM(n_lstm_cells)(rnn_tower)

		rnn_tower = Dropout(dropout_rate)(rnn_tower)

		''' Create the Convolution Tower for the MLSTM-FCN Architecture '''
		conv1d_tower = Permute(permute_dims)(input_layer)

		for kl, (conv1d_depth, conv1d_kernel) in enumerate(zip(conv1d_depths, conv1d_kernels)):
			# Loop over all convolution kernel sizes and depths to create the Convolution Tower
			conv1d_tower = Conv1D_Stack(conv1d_tower, 
										conv1d_depth = conv1d_depth, 
										conv1d_kernel = conv1d_kernel, 
										activation_func = activation_func, 
										local_initializer = local_initializer) 

			if Squeeze:
				conv1d_tower = squeeze_excite_block(conv1d_tower, 
											squeeze_ratio=squeeze_ratio, 
											activation_func=activation_func, 
											logit_output=logit_output, 
											kernel_initializer=squeeze_initializer,
											use_bias=use_bias)

			# Turn off Squeeze after the second to last layer
			#	to avoid Squeezing at the last layer
			if kl + 2 == len(conv1d_kernels): Squeeze = False
		
		conv1d_tower = GlobalAveragePooling1D()(conv1d_tower)

		output_layer = concatenate([rnn_tower, conv1d_tower])
		output_layer = Dense(self.num_classes, 
								activation=logit_output)(output_layer)
		
		self.model = Model(input_layer, output_layer)

		if verbose: self.model.summary()
		
		# add load model code ere to fine-tune

	def load_dataset(load_train_filename, load_test_filename, feature_columns=[], 
						is_timeseries = True, normalize_timeseries=False, 
						x_tol = 1e-8, verbose = True) -> (np.array, np.array):

		self.is_timeseries = is_timeseries
		self.normalize_timeseries = normalize_timeseries
		self.load_train_filename = load_train_filename
		self.load_test_filename = load_test_filename

	    if verbose: 
	    	print("Loading training data at: ", self.load_train_filename)
	    	print("Loading testing data at: ", self.load_test_filename)

	    if not os.path.exists(load_train_filename):
	        raise FileNotFoundError('File {} not found!'.format(self.load_train_filename))
		
		if not os.path.exists(load_test_filename):
	        raise FileNotFoundError('File {} not found!'.format(self.load_test_filename))

	    self.X_train, self.y_train = joblib.load(self.load_train_filename)
	    self.X_test, self.y_test = joblib.load(self.load_test_filename)

	    self.max_nb_variables = X_train.shape[1]
	    self.max_timesteps = X_train.shape[-1]

	    # extract labels Y and normalize to [0 - (MAX - 1)] range
	    self.num_classes = len(np.unique(self.y_train))
	    self.y_train = (self.y_train - self.y_train.min()) / (self.y_train.max() - self.y_train.min()) * (self.num_classes - 1)

	    # scale the values
	    if self.is_timeseries and self.normalize_timeseries:
	        X_train_mean = self.X_train.mean()
	        X_train_std = self.X_train.std()
	        self.X_train = (self.X_train - X_train_mean) / (X_train_std + x_tol)

	    if verbose: print("Finished processing train dataset..")

	    # extract labels Y and normalize to [0 - (MAX - 1)] range
	    # self.num_classes = len(np.unique(y_test))
	    self.y_test = (self.y_test - self.y_test.min()) / (self.y_test.max() - self.y_test.min()) * (self.num_classes - 1)

        # scale the values
        if self.is_timeseries and self.normalize_timeseries:
            self.X_test = (self.X_test - X_train_mean) / (X_train_std + x_tol)

	    if verbose:
	        print("Finished loading test dataset..")
	        print()
	        print("Number of train samples : ", self.X_train.shape[0])
	        print("Number of test samples : ", self.X_test.shape[0])
	        print("Number of classes : ", self.num_classes)
	        print("Sequence length : ", self.X_train.shape[-1])

	def train_model(self, epochs=50, batch_size=128, val_subset=None, cutoff=None, 
						dataset_prefix='rename_me_', dataset_fold_id=None, 
						learning_rate=1e-3, monitor='loss', optimization_mode='auto', 
						compute_class_weights=True, compile_model=True):

	    self._classes = np.unique(self.y_train)
	    self._lbl_enc = LabelEncoder()
	    
	    y_ind = self._lbl_enc.fit_transform(self.y_train.ravel())
	    
	    if compute_class_weights:
		    recip_freq = len(self.y_train) / (len(self._lbl_enc.classes_) *
		                           np.bincount(y_ind).astype(np.float64))

		    self._class_weight = recip_freq[self._lbl_enc.transform(self.classes)]
		else:
			self._class_weight = np.ones(self.y_train.size)

	    print("Class weights : ", self._class_weight)

	    self.y_train = to_categorical(self.y_train, len(np.unique(self.y_train)))
	    self.y_test = to_categorical(self.y_test, len(np.unique(self.y_test)))

	    if self.is_timeseries:
	        factor = 1. / np.cbrt(2)
	    else:
	        factor = 1. / np.sqrt(2)

	    if dataset_fold_id is None:
	        self.weight_fn = "./weights/{}_weights.h5".format(dataset_prefix)
	    else:
	        self.weight_fn = "./weights/{}_fold_{}_weights.h5".format(dataset_prefix, dataset_fold_id)

	    model_checkpoint = ModelCheckpoint(weight_fn, verbose=1, mode=optimization_mode,
	                                       monitor=monitor, save_best_only=True, save_weights_only=True)

	    reduce_lr = ReduceLROnPlateau(monitor=monitor, patience=100, mode=optimization_mode,
	                                  factor=factor, cooldown=0, min_lr=1e-4, verbose=2)
	    
	    callback_list = [model_checkpoint, reduce_lr]

	    optm = Adam(lr=learning_rate)

	    if compile_model:
	        model.compile(optimizer=optm, loss='categorical_crossentropy', metrics=['accuracy'])

	    if val_subset is not None:
	        X_test = X_test[:val_subset]
	        y_test = y_test[:val_subset]

	    model.fit(X_train, y_train, batch_size=batch_size, epochs=epochs, callbacks=callback_list,
	              class_weight=class_weight, verbose=2, validation_data=(X_test, y_test))

	def evaluate_model(self, test_data_subset=None, cutoff=None, dataset_fold_id=None):
		
	    if max_nb_variables != MAX_NB_VARIABLES[dataset_id]:
	        if cutoff is None:
	            choice = cutoff_choice(dataset_id, max_nb_variables)
	        else:
	            assert cutoff in ['pre', 'post'], 'Cutoff parameter value must be either "pre" or "post"'
	            choice = cutoff

	        if choice not in ['pre', 'post']:
	            return
	        else:
	            _, X_test = cutoff_sequence(None, X_test, choice, dataset_id, max_nb_variables)

	    if not is_timeseries:
	        X_test = pad_sequences(X_test, maxlen=MAX_NB_VARIABLES[dataset_id], padding='post', truncating='post')
	    y_test = to_categorical(y_test, len(np.unique(y_test)))

	    optm = Adam(lr=1e-3)
	    model.compile(optimizer=optm, loss='categorical_crossentropy', metrics=['accuracy'])

	    if dataset_fold_id is None:
	        self.weight_fn = "./weights/{}_weights.h5".format(dataset_prefix)
	    else:
	        self.weight_fn = "./weights/{}_fold_{}_weights.h5".format(dataset_prefix, dataset_fold_id)

	    model.load_weights(weight_fn)

	    if test_data_subset is not None:
	        X_test = X_test[:test_data_subset]
	        y_test = y_test[:test_data_subset]

	    print("\nEvaluating : ")
	    loss, accuracy = model.evaluate(X_test, y_test, batch_size=batch_size)
	    print()
	    print("Final Accuracy : ", accuracy)

if __name__ == "__main__":

	data_set_name = 'plasticc'
	
	dataset_settings = dict(n_lstm_cells = 8, 
							dropout_rate = 0.8, 
							permute_dims = (2,1), 
							conv1d_depths = [128, 256, 128], 
							conv1d_kernels = [8, 5, 3], 
							local_initializer = 'he_uniform', 
							activation_func = 'relu', 
							squeeze_ratio = 16, 
							logit_output = 'sigmoid', 
							squeeze_initializer = 'he_normal', 
							use_bias = False, 
							verbose = False)
			 				# Attention = False, # Default
			 				# Squeeze = True,  # Default
	
	normalize_timeseries = True

	X_train, y_train, X_test, y_test, is_timeseries = load_dataset_at(load_data_filename, 
                                                        normalize_timeseries=normalize_timeseries)

	# Model 1
	instance1 = MLSTM_FCN(DATASET_INDEX=DATASET_INDEX)

	instance1.create_model(**dataset_settings)
	instance1.load_dataset()
	instance1.train_model(epochs=1000, batch_size=128)
	instance1.evaluate_model(batch_size=128)
	
	# # Model 2
	# instance2 = MLSTM_FCN(DATASET_INDEX=DATASET_INDEX)
	# instance2.create_model(Attention=True, **dataset_settings)

	# # Model 3
	# instance3 = MLSTM_FCN(DATASET_INDEX=DATASET_INDEX)
	# instance3.create_model(Squeeze=False, **dataset_settings)

	# # Model 4
	# instance4 = MLSTM_FCN(DATASET_INDEX=DATASET_INDEX)
	# instance4.create_model(Attention=True, Squeeze=False, **dataset_settings)

	# train_model(instance1.model, X_train, y_train, X_test, y_test, is_timeseries, epochs=1000, batch_size=128)
	# evaluate_model(instance1.model, X_test, y_test, is_timeseries, batch_size=128)