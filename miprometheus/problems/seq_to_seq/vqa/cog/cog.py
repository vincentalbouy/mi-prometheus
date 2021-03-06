 #!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
#
# Copyright (C) IBM Corporation 2018
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""cog.py: Implementation of Google's COG dataset. https://arxiv.org/abs/1803.06092"""

__author__ = "Emre Sevgen"

import torch
import gzip
import json
import os
import tarfile
import numpy as np

from miprometheus.problems.seq_to_seq.vqa.vqa_problem import VQAProblem
from miprometheus.problems.seq_to_seq.vqa.cog.cog_utils import json_to_img as jti


class COG(VQAProblem):
	"""
	The COG dataset is a sequential VQA dataset.

	Inputs are a sequence of images of simple shapes and characters on a black background, \
	and a question based on these objects that relies on memory which has to be answered at every step of the sequence.

	See https://arxiv.org/abs/1803.06092 (`A Dataset and Architecture for Visual Reasoning with a Working Memory`)\
	for the reference paper.

	"""

	def __init__(self, params):
		"""
		Initializes the :py:class:`COG` problem:

			- Calls :py:class:`miprometheus.problems.VQAProblem` class constructor,
			- Sets the following attributes using the provided ``params``:

				- ``self.data_folder`` (`string`) : Data directory where the dataset is stored.
				- ``self.set`` (`string`) : 'val', 'test', or 'train'
				- ``self.tasks`` (`string` or list of `string`) : Which tasks to use. 'class', 'reg', \
				'all', or a list of tasks such as ['AndCompareColor', 'AndCompareShape']. \
				Only the selected tasks will be used.
				- ``self.dataset_type`` (`string`) : Which dataset to use, 'canonical', 'hard', or \
				'generated'. If 'generated', please specify 'examples_per_task', 'sequence_length', \
				'memory_length', and 'max_distractors' under 'generation'. Can also specify 'nr_processors' for generation.

			- Adds the following as default params:

				>>> {'data_folder': os.path.expanduser('~/data/cog'),
				>>>  'set': 'train',
				>>>  'tasks': 'class',
				>>>  'dataset_type': 'canonical',
				>>>  'initialization_only': False}

			- Sets:

				>>> self.data_definitions = {'images': {'size': [-1, self.sequence_length, 3, self.img_size, self.img_size], 'type': [torch.Tensor]},
				>>>                          'tasks': {'size': [-1, 1], 'type': [list, str]},
				>>>                          'questions': {'size': [-1, 1], 'type': [list, str]},
				>>>                          'targets_reg': {'size': [-1, self.sequence_length, 2], 'type': [torch.Tensor]},
				>>>                          'targets_class': {'size': [-1, self.sequence_length, 1], 'type' : [list,str]}
				>>>                         }



		:param params: Dictionary of parameters (read from configuration ``.yaml`` file).
		:type params: :py:class:`miprometheus.utils.ParamInterface`

		"""
	
		# Call base class constructors
		super(COG, self).__init__(params)

		# Set default parameters.
		self.params.add_default_params({'data_folder': os.path.expanduser('~/data/cog'), 'set': 'train',
										'tasks': 'class',
										'dataset_type': 'canonical',
										'initialization_only': False})

		# Retrieve parameters from the dictionary
		# Data folder main is /path/cog
		# Data folder parent is data_X_Y_Z
		# Data folder children are train_X_Y_Z, test_X_Y_Z, or val_X_Y_Z
		self.data_folder_main = os.path.expanduser(params['data_folder'])

		self.set = params['set']
		assert self.set in ['val', 'test', 'train'], "set in configuration file must be one of 'val', 'test', or " \
													 "'train', got {}".format(self.set)
		self.dataset_type = params['dataset_type']
		assert self.dataset_type in ['canonical', 'hard', 'generated'], "dataset in configuration file must be one of " \
																		"'canonical', 'hard', or 'generated', got {}".format(self.dataset_type)

		# Parse task and dataset_type
		self.parse_tasks_and_dataset_type(params)
	
		# Name
		self.name = 'COG'

		# Get the "hardcoded" image width/height.
		self.img_size = 112  # self.params['img_size']

		# Set default values
		self.default_values = {'height': self.img_size,
							   'width': self.img_size,
							   'num_channels': 3,
							   'sequence_length' : self.sequence_length}
		
		# Set data dictionary based on parsed dataset type
		self.data_definitions = {'images': {'size': [-1, self.sequence_length, 3, self.img_size, self.img_size], 'type': [torch.Tensor]},
								 'tasks': {'size': [-1, 1], 'type': [list, str]},
								 'questions': {'size': [-1, 1], 'type': [list, str]},
								 'targets_reg': {'size': [-1, self.sequence_length, 2], 'type': [torch.Tensor]},
								 'targets_class': {'size': [-1, self.sequence_length, 1], 'type' : [list,str]}
								 }

		# Check if dataset exists, download or generate if necessary.
		self.source_dataset()

		if not params['initialization_only']:

			# Load all the .jsons, but image generation is done in __getitem__
			self.dataset = {}
			for task in self.tasks:
				self.dataset[task] = []
			self.length = 0
	
			self.logger.info("Loading dataset as json into memory.")
			# Val and Test are not shuffled
			if self.set == 'val' or self.set == 'test':
				for tasklist in os.listdir(self.data_folder_child):
					if tasklist[4:-8] in self.tasks:
						with gzip.open(os.path.join(self.data_folder_child,tasklist)) as f:
							fulltask = f.read().decode('utf-8').split('\n')
							for datapoint in fulltask:
								self.dataset[tasklist[4:-8]].append(json.loads(datapoint))
						self.length = self.length + len(self.dataset[tasklist[4:-8]])
						self.logger.info("{} task examples loaded.".format(tasklist[4:-8]))
					else:
						self.logger.info("Skipped loading {} task.".format(tasklist[4:-8]))
		
			# Training set is shuffled
			elif self.set == 'train':
				for zipfile in os.listdir(self.data_folder_child):
					with gzip.open(os.path.join(self.data_folder_child,zipfile)) as f:
						fullzip = f.read().decode('utf-8').split('\n')
						for datapoint in fullzip:
							task = json.loads(datapoint)
							if task['family'] in self.tasks:
								self.dataset[task['family']].append(task)
								self.length = self.length + 1
					self.logger.info("Zip file {} loaded.".format(zipfile))

		else:
			self.logger.info("COG initialization complete.")
			exit(0)

	def __getitem__(self, index):
		"""
		Getter method to access the dataset and return a sample.

		:param index: index of the sample to return.
		:type index: int

		:return: ``DataDict({'images', 'questions', 'targets', 'targets_label'})``, with:
		
			- ``images``: Sequence of images,
			- ``tasks``: Which task family sample belongs to,
			- ``questions``: Question on the sequence (this is constant per sequence for COG),
			- ``targets_reg``: Sequence of targets as tuple of floats for pointing tasks,
			- ``targets_class``: Sequence of word targets for classification tasks.

		"""

		# With the assumption that each family has the same number of examples
		i = index % len(self.tasks)
		j = int(index / len(self.tasks))

		# This returns:
		# All variables are numpy array of float32
		# in_imgs: (n_epoch*batch_size, img_size, img_size, 3)
		# in_rule: (max_seq_length, batch_size) the rule language input, type int32
		# seq_length: (batch_size,) the length of each task instruction
		# out_pnt: (n_epoch*batch_size, n_out_pnt)
		# out_pnt_xy: (n_epoch*batch_size, 2)
		# out_word: (n_epoch*batch_size, n_out_word)
		# mask_pnt: (n_epoch*batch_size)
		# mask_word: (n_epoch*batch_size)

		output = jti.json_to_feeds([self.dataset[self.tasks[i]][j]])[0]
		images = ((torch.from_numpy(output)).permute(1, 0, 4, 2, 3)).squeeze()
				
		data_dict = self.create_data_dict()
		data_dict['images'] = images
		data_dict['tasks'] = [self.tasks[i]]
		data_dict['questions'] = [self.dataset[self.tasks[i]][j]['question']]
		answers = self.dataset[self.tasks[i]][j]['answers']

		if self.tasks[i] in self.classification_tasks:
			data_dict['targets_reg'] = torch.FloatTensor([0, 0]).expand(self.sequence_length,2)
			data_dict['targets_class'] = answers
		else:
			data_dict['targets_reg'] = torch.FloatTensor([[-1, -1] if reg == 'invalid' else reg for reg in answers])
			data_dict['targets_class'] = [' ' for _ in answers]

		return data_dict

	def collate_fn(self, batch):
		"""
		Combines a list of :py:class:`miprometheus.utils.DataDict` (retrieved with :py:func:`__getitem__`) into a batch.

		:param batch: individual :py:class:`miprometheus.utils.DataDict` samples to combine.
		:type batch: list

		:return: ``DataDict({'images', 'tasks', 'questions', 'targets_reg', 'targets_class'})`` containing the batch.

		"""
		data_dict = self.create_data_dict()
		
		data_dict['images'] = torch.stack([image['images'] for image in batch]).type(torch.FloatTensor)
		data_dict['tasks'] = [task['tasks'] for task in batch]
		data_dict['questions'] = [question['questions'] for question in batch]
		data_dict['targets_reg'] = torch.stack([reg['targets_reg'] for reg in batch]).type(torch.FloatTensor)
		data_dict['targets_class'] = [tgclassif['targets_class'] for tgclassif in batch]

		return data_dict

	def parse_tasks_and_dataset_type(self, params):
		"""
		Parses the task list and dataset type. Then sets folder paths to appropriate values.

		:param params: Dictionary of parameters (read from the configuration ``.yaml`` file).
		:type params: :py:class:`miprometheus.utils.ParamInterface`

		"""

		self.classification_tasks = ['AndCompareColor', 'AndCompareShape', 'AndSimpleCompareColor',
									 'AndSimpleCompareShape', 'CompareColor', 'CompareShape', 'Exist',
									 'ExistColor', 'ExistColorOf', 'ExistColorSpace', 'ExistLastColorSameShape',
									 'ExistLastObjectSameObject', 'ExistLastShapeSameColor', 'ExistShape',
									 'ExistShapeOf', 'ExistShapeSpace', 'ExistSpace', 'GetColor', 'GetColorSpace',
									 'GetShape', 'GetShapeSpace', 'SimpleCompareColor', 'SimpleCompareShape']

		self.regression_tasks = ['AndSimpleExistColorGo', 'AndSimpleExistGo', 'AndSimpleExistShapeGo', 'CompareColorGo',
								 'CompareShapeGo', 'ExistColorGo', 'ExistColorSpaceGo', 'ExistGo', 'ExistShapeGo',
								 'ExistShapeSpaceGo', 'ExistSpaceGo', 'Go', 'GoColor', 'GoColorOf', 'GoShape',
								 'GoShapeOf', 'SimpleCompareColorGo', 'SimpleCompareShapeGo', 'SimpleExistColorGo',
								 'SimpleExistGo','SimpleExistShapeGo']

		self.tasks = params['tasks']
		if self.tasks == 'class':
			self.tasks = self.classification_tasks
		elif self.tasks == 'reg':
			self.tasks = self.regression_tasks
		elif self.tasks == 'all':
			self.tasks = self.classification_tasks + self.regression_tasks

		# If loading a default dataset, set default path names and set sequence length		
		if self.dataset_type == 'canonical':
			self.examples_per_task = 227280
			self.sequence_length = 4
			self.memory_length = 3
			self.max_distractors = 1
		elif self.dataset_type == 'hard':
			self.examples_per_task = 227280
			self.sequence_length = 8
			self.memory_length = 7
			self.max_distractors = 10
		elif self.dataset_type == 'generated':
			self.params.add_default_params({'generation':{'nr_processors':1}})
			try:
				self.examples_per_task = int(params['generation']['examples_per_task'])
				self.sequence_length = int(params['generation']['sequence_length'])
				self.memory_length = int(params['generation']['memory_length'])
				self.max_distractors = int(params['generation']['max_distractors'])
				self.nr_processors = int(params['generation']['nr_processors'])
			except KeyError:
				self.logger.info("Please specify examples per task, sequence length, memory length and maximum distractors "
					  "for a generated dataset under 'dataset_type'.")
				exit(1)
			except ValueError:
				self.logger.info("Examples per task, sequence length, memory length, maximum distractors and nr_processors "
					  "(if provided) must be of type int.")
				exit(2)

		self.dataset_name = str(self.sequence_length)+'_'+str(self.memory_length)+'_'+str(self.max_distractors)
		self.data_folder_parent = os.path.join(self.data_folder_main,'data_'+self.dataset_name) 
		self.data_folder_child = os.path.join(self.data_folder_parent,self.set+'_'+self.dataset_name)
		
	def source_dataset(self):
		"""
		Handles downloading and unzipping the canonical or hard version of the dataset.

		"""
		if self.dataset_type == 'canonical':
			self.download = self.CheckAndDownload(self.data_folder_child, 
												  'https://storage.googleapis.com/cog-datasets/data_4_3_1.tar')
		
		elif self.dataset_type == 'hard':
			self.download = self.CheckAndDownload(self.data_folder_child,
												  'https://storage.googleapis.com/cog-datasets/data_8_7_10.tar')
		if self.download:
			self.logger.info('\nDownload complete. Extracting...')
			tar = tarfile.open(os.path.expanduser('~/data/downloaded'))
			tar.extractall(path=self.data_folder_main)
			tar.close()
			self.logger.info('\nDone! Cleaning up.')
			os.remove(os.path.expanduser('~/data/downloaded'))
			self.logger.info('\nClean-up complete! Dataset ready.')

		else:
			self.download = self.check_and_download(self.data_folder_child)
			if self.download:			
				from miprometheus.problems.seq_to_seq.vqa.cog.cog_utils import generate_dataset
				generate_dataset.main(self.data_folder_parent,
															self.examples_per_task, 
															self.sequence_length, 
															self.memory_length, 
															self.max_distractors,
															self.nr_processors)
				self.logger.info('\nDataset generation complete for {}!'.format(self.dataset_name))

	def add_statistics(self, stat_col):
		"""
		Add :py:class:`COG`-specific stats to :py:class:`miprometheus.utils.StatisticsCollector`.
		
		:param stat_col: :py:class:`miprometheus.utils.StatisticsCollector`.
		
		"""
		stat_col.add_statistic('seq_len', '{:06d}')
		stat_col.add_statistic('max_mem', '{:06d}')
		stat_col.add_statistic('max_distractors', '{:06d}')
		stat_col.add_statistic('task', '{}')

	def collect_statistics(self, stat_col, data_dict, logits):
		"""
		Collects dataset details.

		:param stat_col: :py:class:`miprometheus.utils.StatisticsCollector`.
		:param data_dict: :py:class:`miprometheus.utils.DataDict` containing targets.
		:param logits: Prediction of the model (:py:class:`torch.Tensor`)

		"""
		stat_col['seq_len'] = self.sequence_length
		stat_col['max_mem'] = self.memory_length
		stat_col['max_distractors'] = self.max_distractors
		stat_col['task'] = data_dict['tasks']		
		

if __name__ == "__main__":
	
	""" 
	Unit test that checks data dimensions match expected values, and generates an image.
	Checks one regression and one classification task.
	"""

	# Test parameters
	batch_size = 44
	sequence_nr = 1

	# Timing test parameters
	timing_test = True
	testbatches = 100

	# -------------------------

	# Define useful params
	from miprometheus.utils.param_interface import ParamInterface
	params = ParamInterface()
	tasks = ['Go', 'CompareColor']
	params.add_config_params({'data_folder': os.path.expanduser('~/data/cog'),
							  'set': 'val',
							  'dataset_type': 'canonical',
							  'tasks': tasks})

	# Create problem - task Go
	cog_dataset = COG(params)

	# Get a sample - Go
	sample = cog_dataset[0]
	print(repr(sample))

	# Test whether data structures match expected definitions
	assert sample['images'].shape == torch.ones((4, 3, 112, 112)).shape
	assert sample['tasks'] == ['Go']
	assert sample['questions'] == ['point now beige u']
	assert sample['targets_reg'].shape == torch.ones((4, 2)).shape
	assert len(sample['targets_class']) == 4
	assert sample['targets_class'][0] == ' '  

	# Get another sample - CompareColor
	sample2 = cog_dataset[1]
	print(repr(sample2))

	# Test whether data structures match expected definitions
	assert sample2['images'].shape == torch.ones((4, 3, 112, 112)).shape
	assert sample2['tasks'] == ['CompareColor']
	assert sample2['questions'] == ['color of latest g equal color of last1 v ?']
	assert sample2['targets_reg'].shape == torch.ones((4, 2)).shape
	assert len(sample2['targets_class']) == 4
	assert sample2['targets_class'][0] == 'invalid'  
	
	print('__getitem__ works')
	
	# Set up Dataloader iterator
	from torch.utils.data import DataLoader
	
	dataloader = DataLoader(dataset=cog_dataset, collate_fn=cog_dataset.collate_fn,
							batch_size=batch_size, shuffle=False, num_workers=8)

	# Display single sample (0) from batch.
	batch = next(iter(dataloader))

	# Test whether batches are formed correctly	
	assert batch['images'].shape == torch.ones((batch_size,4,3,112,112)).shape
	assert len(batch['tasks']) == batch_size
	assert len(batch['questions']) == batch_size
	assert batch['targets_reg'].shape == torch.ones((batch_size,4,2)).shape
	assert len(batch['targets_class']) == batch_size
	assert len(batch['targets_class'][0]) == 4 

	# VQA expects 'targets', so change 'targets_class' to 'targets'
	# Implement a data_dict.pop later.
	batch['targets'] = batch['targets_reg']
	batch['targets_label'] = batch['targets_class']

	# Convert image to uint8
	batch['images'] = batch['images']/(np.iinfo(np.uint16).max)*255

	# Show sample - Task 1
	cog_dataset.show_sample(batch,0,sequence_nr)

	# Show sample - Task 2
	cog_dataset.show_sample(batch,1,sequence_nr)	

	print('Unit test completed')

	if timing_test:
		# Test speed of generating images vs preloading generated images.
		import time

		# Define params to load entire dataset - all tasks included
		params = ParamInterface()
		params.add_config_params({
			'data_folder': '~/data/cog/',
			'set': 'val',
			'dataset_type': 'canonical',
			'tasks': 'all'})

		preload = time.time()
		full_cog_canonical = COG(params)
		postload = time.time() 

		dataloader = DataLoader(dataset=full_cog_canonical, collate_fn=full_cog_canonical.collate_fn,
								batch_size=batch_size, shuffle=True, num_workers=8)

		prebatch = time.time()
		for i, batch in enumerate(dataloader):
			if i == testbatches:
				break
			if i% 100 == 0:
				print('Batch # {} - {}'.format(i, type(batch)))
		postbatch = time.time()
	
		print('Number of workers: {}'.format(dataloader.num_workers))
		print('Time taken to load the dataset: {}s'.format(postload - preload))	
		print('Time taken to exhaust {} batches for a batch size of {} with image generation: {}s'.format(testbatches,
																										  batch_size,
																										  postbatch-prebatch))
	
		# Test pregeneration and loading
		for i, batch in enumerate(dataloader):
			if i == testbatches:
				print('Finished saving {} batches'.format(testbatches))
				break
			if not os.path.exists(os.path.expanduser('~/data/cogtest')):
				os.makedirs(os.path.expanduser('~/data/cogtest'))
			np.save(os.path.expanduser('~/data/cogtest/'+str(i)),batch['images'])

		preload = time.time()
		for i in range(testbatches):
			mockload = np.fromfile(os.path.expanduser('~/data/cogtest/'+str(i)+'.npy'))
		postload = time.time()
		print('Generation time for {} batches: {}, Load time for {} batches: {}'.format(testbatches, postbatch-prebatch, 
												testbatches, postload-preload))

		print('Timing test completed, removing files.')
		for i in range(testbatches):
			os.remove(os.path.expanduser('~/data/cogtest/'+str(i)+'.npy'))
	
	print('Done!')
