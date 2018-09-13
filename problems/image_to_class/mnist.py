#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

"""mnist.py: contains code of loading MNIST dataset using torchvision"""
__author__ = "Younes Bouhadjar"
from torchvision import datasets, transforms
import torch.nn.functional as F

from problems.problem import DataDict
from problems.image_to_class.image_to_class_problem import ImageToClassProblem


class MNIST(ImageToClassProblem):
    """
    Classic MNIST classification problem.
    """

    def __init__(self, params):
        """
        Initializes the MNIST dataset, calls base class initialization, sets
        properties using the provided parameters.

        TODO: DOCUMENTATION

        :param params: Dictionary of parameters (read from configuration file).

        """

        # Call base class constructors.
        super(MNIST, self).__init__(params)

        # Retrieve parameters from the dictionary.
        self.use_train_data = params['use_train_data']
        self.root_dir = params['mnist_folder']

        self.padding = params['padding']
        # up scaling the image to 224, 224 if True
        self.up_scaling = params['up_scaling']

        # define the default_values dict: holds parameters values that a model may need.
        self.default_values = {'nb_classes': 10}

        self.name = 'MNIST'

        # Define transforms: takes in an PIL image and returns a transformed version
        transform = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor()]) \
            if self.up_scaling else transforms.Compose([transforms.ToTensor()])

        # load the dataset
        self.dataset = datasets.MNIST(root=self.root_dir, train=self.use_train_data, download=True,
                                            transform=transform)
        # type(self.train_dataset) = <class 'torchvision.datasets.mnist.MNIST'>
        # -> inherits from torch.utils.data.Dataset

        self.length = len(self.dataset)

        # Class names.
        self.labels = 'Zero One Two Three Four Five Six Seven Eight Nine'.split(
            ' ')

    def __getitem__(self, index):
        """
        Getter method to access the dataset and return a sample.

        :param index: index of the sample to return.

        :return: DataDict({'images','targets', 'targets_label'}), with:

            - images: Image (representation of a 0 - 9 digit), transformed if a transform is specified in ``__init__``.
            - targets: Index of the target class
            - targets_label: Label of the target class (cf ``self.labels``)


        """

        img, target = self.dataset.__getitem__(index)

        # pad img
        img = F.pad(img, self.padding, 'constant', 0)

        label = self.labels[target.data]

        data_dict = DataDict({key: None for key in self.data_definitions.keys()})
        data_dict['images'] = img
        data_dict['targets'] = target
        data_dict['targets_label'] = label

        return data_dict

    def collate_fn(self, batch):
        """
        Combines a list of DataDict (retrieved with __getitem__) into a batch.

        .. note::

            This function wraps a call to ``default_collate`` and simply returns the batch as a DataDict\
            instead of a dict.
            Multi-processing is supported as the data sources are small enough to be kept in memory\
            (`training.pt` has a size of 47.5 MB).

        :param batch: list of individual ``DataDict`` samples to combine.

        :return: DataDict({'images','targets', 'targets_label'}) containing the batch.

        """

        return DataDict({key: value for key, value in zip(self.data_definitions.keys(),
                                                          super(MNIST, self).collate_fn(batch).values())})


if __name__ == "__main__":
    """ Tests sequence generator - generates and displays a random sample"""

    # "Loaded parameters".
    from utils.param_interface import ParamInterface 
    params = ParamInterface()
    params.add_default_params({
        'batch_size': 64,
        'use_train_data': True,
        'mnist_folder': '~/data/mnist',
        'padding': [4, 4, 3, 3],
        'up_scaling': False})

    # Create problem object.
    mnist = MNIST(params)
    sample = mnist[10]
    print(type(sample))
    print('__getitem__ works.')

    # wrap DataLoader on top of this Dataset subclass
    from torch.utils.data.dataloader import DataLoader
    dataloader = DataLoader(dataset=mnist, collate_fn=mnist.collate_fn,
                            batch_size=params['batch_size'], shuffle=True, num_workers=8)

    # try to see if there is a speed up when generating batches w/ multiple workers

    # HAVE TO MANAGE INDEXES FOR THE TRAINING / VALIDATION SPLIT

    import time
    s = time.time()
    for i, batch in enumerate(dataloader):
        print('Batch # {} - {}'.format(i, type(batch)))

    print('Number of workers: {}'.format(dataloader.num_workers))
    print('time taken to exhaust the dataset for a batch size of {}: {}s'.format(params['batch_size'], time.time()-s))

    # Display single sample (0) from batch.
    #batch = next(iter(dataloader))
    #mnist.show_sample(batch, 0)
