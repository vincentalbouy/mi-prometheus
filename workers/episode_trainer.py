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

"""
episode_trainer.py:

    - This file contains the implementation of the ``EpisodeTrainer``, which inherits from ``Trainer``.

"""
__author__ = "Vincent Marois, Tomasz Kornuta"

import argparse
from torch.nn.utils import clip_grad_value_

import workers.worker as worker
import workers.trainer as trainer
from workers.trainer import Trainer
from utils.worker_utils import forward_step, validation,  validate_over_set


class EpisodeTrainer(Trainer):
    """
    Implementation for the episode-based Trainer.

    ..note::

        The default ``Trainer`` is based on epochs. While an epoch can be defined for all finite-size datasets,\
         it makes less sense for problems which have a very large, almost infinite, dataset (like algorithmic \
         tasks, which generate random data on-the-fly). This is why this episode Trainer is implemented.
         Instead of looping on epochs, it iterates directly on episodes (we call an iteration on a single batch\
          an episode).


    """

    def __init__(self, flags: argparse.Namespace):
        """
        Only calls the ``Trainer`` constructor as the initialization phase is identical to the ``Trainer``.

        :param flags: Parsed arguments from the parser.

        """
        # call base constructor
        super(EpisodeTrainer, self).__init__(flags=flags)

        # set logger name
        self.name = 'EpisodeTrainer'
        self.set_logger_name(self.name)

        # Set the Model validation frequency for the ``EpisodicTrainer`` (Default: 100 episodes).
        self.params['validation'].add_default_params({'interval': 100})
        self.model_validation_interval = self.params['validation']['interval']

        # generate one batch used for validation
        self.data_valid = next(iter(self.dl_valid))


    def initialize_statistics_collection(self):
        """
        Function initializes all statistics collectors and aggregators used by a given worker,
        creates output files etc.
        """
        # Create the csv file to store the training statistics.
        self.training_stats_file = self.stat_col.initialize_csv_file(self.log_dir, 'training_statistics.csv')

        # Create the csv file to store the validation statistics.
        self.validation_stats_file = self.stat_col.initialize_csv_file(self.log_dir, 'validation_statistics.csv')

        # Create the csv file to store the validation statistic aggregations.
        self.validation_stats_aggregated_file = self.stat_agg.initialize_csv_file(self.log_dir, 'validation_statistics_aggregated.csv')


    def finalize_statistics_collection(self):
        """
        Finalizes statistics collection, closes all files etc.
        """
        # Close all files.
        self.training_stats_file.close()
        self.validation_stats_file.close()
        self.validation_stats_aggregated_file.close()


    def forward(self, flags: argparse.Namespace):
        """
        Main function of the ``EpisodeTrainer``.

        Iterates over the (cycled) DataLoader (one iteration = one episode).

        .. note::

            The test for terminal conditions (e.g. convergence) is done at the end of each episode. \
            The terminal conditions are as follows:

                 - The loss is below the specified threshold (using the validation loss or the highest training loss\
                  over several episodes),
                  - The maximum number of episodes has been met,
                  - The user pressed 'Quit' during visualization (TODO: should change that)


        The function does the following for each episode:

            - Handles curriculum learning if set,
            - Resets the gradients
            - Forwards pass of the model,
            - Logs statistics and exports to TensorBoard (if set),
            - Computes gradients and update weights
            - Activate visualization if set,
            - Validate the model on a batch according to the validation frequency.
            - Checks the above terminal conditions.


        :param flags: Parsed arguments from the parser.

        """
        # Ask for confirmation - optional.
        if flags.confirm:
            input('Press any key to continue')

        # Flag denoting whether we converged (or reached last episode).
        terminal_condition = False

        # cycle the DataLoader -> infinite iterator
        self.dataloader = self.cycle(self.dataloader)

        '''
        Main training and validation loop.
        '''
        episode = 0
        for data_dict in self.dataloader:

            # reset all gradients
            self.optimizer.zero_grad()

            # Check the visualization flag - Set it if visualization is wanted during training & validation episodes.
            if flags.visualize is not None and flags.visualize <= 1:
                self.app_state.visualize = True
            else:
                self.app_state.visualize = False

            # Turn on training mode for the model.
            self.model.train()

            # 1. Perform forward step, get predictions and compute loss.
            logits, loss = forward_step(self.model, self.problem, episode, self.stat_col, data_dict)

            # 2. Backward gradient flow.
            loss.backward()

            # Check the presence of the 'gradient_clipping'  parameter.
            try:
                # if present - clip gradients to a range (-gradient_clipping, gradient_clipping)
                val = self.params['training']['gradient_clipping']
                clip_grad_value_(self.model.parameters(), val)

            except KeyError:
                # Else - do nothing.
                pass

            # 3. Perform optimization.
            self.optimizer.step()

            # 4. Log collected statistics.

            # 4.1. Export to csv.
            self.stat_col.export_statistics_to_csv(self.training_stats_file)

            # 4.2. Export data to tensorboard.
            if (flags.tensorboard is not None) and (episode % flags.logging_frequency == 0):
                self.stat_col.export_statistics_to_tensorboard(self.training_writer)

                # Export histograms.
                if flags.tensorboard >= 1:
                    for name, param in self.model.named_parameters():
                        try:
                            self.training_writer.add_histogram(name, param.data.cpu().numpy(), episode, bins='doane')

                        except Exception as e:
                            self.logger.error("  {} :: data :: {}".format(name, e))

                # Export gradients.
                if flags.tensorboard >= 2:
                    for name, param in self.model.named_parameters():
                        try:
                            self.training_writer.add_histogram(name + '/grad', param.grad.data.cpu().numpy(), episode,
                                                               bins='doane')

                        except Exception as e:
                            self.logger.error("  {} :: grad :: {}".format(name, e))

            # 4.3. Log to logger.
            if episode % flags.logging_frequency == 0:
                self.logger.info(self.stat_col.export_statistics_to_string())

                # empty Statistics Collector to avoid memory leak
                self.stat_col.empty()

            # 5. Check visualization of training data.
            if self.app_state.visualize:

                # Allow for preprocessing
                data_dict, logits = self.problem.plot_preprocessing(data_dict, logits)

                # Show plot, if user presses Quit - break.
                if self.model.plot(data_dict, logits):
                    break

            #  6. Validate and (optionally) save the model.
            user_pressed_stop = False

            if (episode % self.model_validation_interval) == 0:

                # Check visualization flag
                if flags.visualize is not None and (1 <= flags.visualize <= 2):
                    self.app_state.visualize = True
                else:
                    self.app_state.visualize = False

                # Perform validation.
                validation_loss, user_pressed_stop = validation(self.model, self.problem_validation, episode,
                                                                self.stat_col, self.data_valid, flags, self.logger,
                                                                self.validation_stats_file, self.validation_writer)

                # Save the model using the latest validation statistics.
                self.model.save(self.model_dir, validation_loss, self.stat_col)

            # 7. Terminal conditions.

            # Apply curriculum learning - change some of the Problem parameters
            self.curric_done = self.problem.curriculum_learning_update_params(episode)

            # 7.1. The User pressed stop during visualization.
            if user_pressed_stop:
                break

            # 7.2. - the loss is < threshold (only when curriculum learning is finished if set.)
            if self.curric_done or not self.must_finish_curriculum:

                # loss_stop = True if convergence
                loss_stop = validation_loss < self.params['training']['terminal_condition']['loss_stop']
                # We already saved that model.

                if loss_stop:
                    # Ok, we have converged.
                    terminal_condition = True
                    # Finish the training.
                    break

            # 7.3. - The episodes number limit has been reached.
            if episode == self.params['training']['terminal_condition']['max_episodes']:
                terminal_condition = True
                # If we reach this condition, then it is possible that the model didn't converge correctly
                # and present poorer performance.

                # We still save the model as it may perform better during this episode
                # (as opposed to the previous episode)

                # Validate on the problem if required - so we can collect the
                # statistics needed during saving of the best model.
                #if self.use_validation_problem:
                _, _ = validation(self.model, self.problem_validation, episode,
                                      self.stat_col, self.data_valid, flags, self.logger,
                                      self.validation_file, self.validation_writer)
                # save the model
                self.model.save(self.model_dir, self.stat_col)

                # "Finish" the training.
                break

            # check if we are at the end of the 'epoch': Indicate that the DataLoader is now cycling.
            if ((episode + 1) % self.problem.get_epoch_size(
                    self.params['training']['problem']['batch_size'])) == 0:
                self.logger.warning('The DataLoader has exhausted -> using cycle(iterable).')

            # Move on to next episode.
            episode += 1

        '''
        End of main training and validation loop.
        '''
        # empty Statistics Collector
        self.stat_col.empty()
        self.logger.info('Emptied StatisticsCollector.')

        # Validate over the entire validation set
        # Check visualization flag - turn on visualization for last validation if needed.
        if flags.visualize is not None and (flags.visualize == 3):
            self.app_state.visualize = True
        else:
            self.app_state.visualize = False

        self.stat_agg['episode'] = episode
        avg_loss_valid, user_pressed_stop = validate_over_set(self.model, self.problem_validation, self.dl_valid,
                                                              self.stat_col, self.stat_agg, flags, self.logger,
                                                              self.validation_stats_aggregated_file, None, 1)

        # Save the model using the average validation loss.
        self.model.save(self.model_dir, avg_loss_valid, self.stat_agg)

        # Check whether we have finished training properly.
        if terminal_condition:

            self.logger.info('Learning finished!')

        else:  # the training did not end properly
            self.logger.warning('Learning interrupted!')

        # And statistics collection.
        self.finalize_statistics_collection()


if __name__ == '__main__':
    # Create parser with list of  runtime arguments.
    argp = argparse.ArgumentParser(formatter_class=argparse.RawTextHelpFormatter)

    # add default arguments
    worker.add_arguments(argp)

    # add trainers-specific arguments
    trainer.add_arguments(argp)

    # Parse arguments.
    FLAGS, unparsed = argp.parse_known_args()

    episode_trainer = EpisodeTrainer(FLAGS)
    # Initialize tensorboard and statistics collection.
    episode_trainer.initialize_tensorboard(FLAGS.tensorboard)
    episode_trainer.initialize_statistics_collection()
    # GO!
    episode_trainer.forward(FLAGS)
