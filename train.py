#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""configuration_test.py: Test of YAML configuration"""
__author__ = "Tomasz Kornuta"

import yaml
import os.path
import argparse
import torch
from torch import nn

# Import problems and problem factory.
from problems.problem_factory import ProblemFactory
# Import models and model factory.
from models.model_factory import ModelFactory

if __name__ == '__main__':

    # Create parser with list of  runtime arguments.
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', type=str, default='', dest='task',
                        help='Name of the task configuration file to be loaded')
    parser.add_argument('-m', action='store_true', dest='mode',
                        help='Mode (TRUE: trains a new model, FALSE: tests existing model)')
    parser.add_argument('-i', type=int, default='100000', dest='iterations', help='Number of training epochs')
    # Parse arguments.
    FLAGS, unparsed = parser.parse_known_args()

    # Check if config file was selected.
    if (FLAGS.task == ''):
        print('Please pass task configuration file as -t parameter')
        exit(-1)
    # Check it file exists.
    if not os.path.isfile(FLAGS.task):
        print('Task configuration file {} does not exists'.format(FLAGS.task))
        exit(-2)

        # Read YAML file
    with open(FLAGS.task, 'r') as stream:
        config_loaded = yaml.load(stream)

    # Print loaded configuration
    # print("Loaded configuration",  config_loaded)
    print("Problem configuration:\n", config_loaded['problem'])
    print("Model configuration:\n", config_loaded['model'])

    # Build problem
    problem = ProblemFactory.build_problem(config_loaded['problem'])

    # Build model
    model, states = ModelFactory.build_model(config_loaded['model'])

    # Run mode: training or inference.
    # if FLAGS.mode:
    #    run_training(FLAGS.iterations,  FLAGS.checkpoint)
    # else:
    #    run_inference(FLAGS.checkpoint)

    # Set loss and optimizer
    criterion = nn.BCELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

    # Start Training
    epoch = 0

    # Data generator : input & target
    for inputs, targets, mask in problem:

        optimizer.zero_grad()

        # apply model
        output, states_test = model(inputs, states)
        # compute loss
        loss = criterion(output[:, mask, :], targets)

        print(", epoch: %d, loss: %1.5f" % (epoch + 1, loss))

        loss.backward()
        optimizer.step()

        if loss < 1e-5:
            path = "./checkpoints/"
            # save model parameters
            torch.save(model.state_dict(), path+"model_parameters")
            break

        epoch += 1

    print("Learning finished!")
