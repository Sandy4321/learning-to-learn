from __future__ import division, print_function
import os
import sys
import warnings
import shutil
import argparse
import numpy as np
import tensorflow as tf
import keras.backend as K
from keras.callbacks import ModelCheckpoint
from sklearn.preprocessing import OneHotEncoder

from learning2learn.models import simple_mlp
from learning2learn.wrangle import (synthesize_data, get_train_test_parameters,
                                    build_train_set_bits,
                                    build_test_trials_o1_bits,
                                    build_test_trials_o2_bits)
from learning2learn.util import (train_model, train_test_split,
                                 evaluate_generalization, add_noise)

def run_experiment(nb_categories, nb_exemplars, params):
    assert nb_categories <= 50
    # enforce a maximum batch size to avoid doing full-batch GD
    batch_size = min(
        params['batch_size'],
        int(np.floor(nb_categories*nb_exemplars/5))
    )
    # Set random seeds
    np.random.seed(0)
    # Create custom TF session
    sess = tf.Session(
        config=tf.ConfigProto(device_count={'GPU': 0})
    )
    K.set_session(sess)
    # Get the shape, color, and texture parameters for the training and
    # testing sets. Features will be drawn from these parameter sets for each
    # sample
    (shape_set_train, shape_set_test), \
    (color_set_train, color_set_test), \
    (texture_set_train, texture_set_test) = \
        get_train_test_parameters(images=False, nb_bits=20)
    if nb_categories < 50:
        shape_set_train, _ = train_test_split(
            shape_set_train,
            50 - nb_categories
        )
        color_set_train, _ = train_test_split(
            color_set_train,
            50 - nb_categories
        )
        texture_set_train, _ = train_test_split(
            texture_set_train,
            50 - nb_categories
        )
    # Build the training set
    print('Building the training set...')
    df_train, labels = synthesize_data(nb_categories, nb_exemplars)
    X_train = build_train_set_bits(
        df_train, shape_set_train, color_set_train,
        texture_set_train
    )
    ohe = OneHotEncoder(sparse=False)
    Y_train = ohe.fit_transform(labels.reshape(-1, 1))
    # Build the test set trials
    print('Building test trials...')
    X_test_order1 = build_test_trials_o1_bits(
        df_train, shape_set_train, shape_set_test, color_set_train,
        color_set_test, texture_set_train, texture_set_test,
        nb_trials=params['nb_test']
    )
    X_test_order2 = build_test_trials_o2_bits(
        shape_set_test, color_set_test, texture_set_test,
        nb_trials=params['nb_test']
    )
    X_test_order1 = add_noise(X_test_order1, p=params['noise'])
    X_test_order2 = add_noise(X_test_order2, p=params['noise'])
    scores_order1 = []
    scores_order2 = []
    for i in range(params['nb_trials']):
        print('Round #%i' % (i+1))
        X_train_noisy = add_noise(X_train, p=params['noise'])
        # Build a neural network model and train it with the training set
        model = simple_mlp(
            nb_in=X_train.shape[-1],
            nb_classes=Y_train.shape[-1]
        )
        # We're going to keep track of the best model throughout training,
        # monitoring the training loss
        weights_file = '../data/mlp_combined.h5'
        if os.path.isfile(weights_file):
            os.remove(weights_file)
        checkpoint = ModelCheckpoint(
            weights_file,
            monitor='loss',
            save_best_only=True,
            save_weights_only=True,
            period=2
        )
        # We'll provide the test set as 'validation data' merely so we can
        # monitor the trajectory... the network won't be using this data.
        train_model(
            model, X_train_noisy, Y_train, epochs=params['nb_epochs'],
            validation_data=None, batch_size=batch_size,
            checkpoint=checkpoint, burn_period=100
        )
        # Now that we've completed all training epochs, let's go ahead and
        # load the best model
        model.load_weights(weights_file)
        # Now evaluate the model on the test data
        score_order1 = evaluate_generalization(
            model, X_test_order1, layer_num=-3,
            batch_size=128
        )
        score_order2 = evaluate_generalization(
            model, X_test_order2, layer_num=-3,
            batch_size=128
        )
        scores_order1.append(score_order1)
        scores_order2.append(score_order2)
        print('\nFirst-order generalization: %0.4f' % score_order1)
        print('\nSecond-order generalization: %0.4f' % score_order2)
    K.clear_session()
    sess.close()

    return np.asarray(scores_order1), np.asarray(scores_order2)

def experiment_loop(exectue_fn, category_trials, exemplar_trials, params,
                    results_path):
    stdout = sys.stdout
    # Create results_path folder. Remove previous one if it already exists.
    if os.path.isdir(results_path):
        warnings.warn('Removing old results folder of the same name!')
        shutil.rmtree(results_path)
    os.mkdir(results_path)
    np.save(os.path.join(results_path, 'category_trials.npy'),
            np.asarray(category_trials))
    np.save(os.path.join(results_path, 'exemplar_trials.npy'),
            np.asarray(exemplar_trials))
    # Loop through different values of (nb_categories, nb_exemplars)
    results_o1_gen = np.zeros(
        shape=(len(category_trials), len(exemplar_trials), params['nb_trials'])
    )
    results_o2_gen = np.zeros(
        shape=(len(category_trials), len(exemplar_trials), params['nb_trials'])
    )
    for i, nb_categories in enumerate(category_trials):
        for j, nb_exemplars in enumerate(exemplar_trials):
            print('Testing for %i categories and %i exemplars...' %
                  (nb_categories, nb_exemplars))
            log_file = os.path.join(results_path,
                                    'log_ca%0.4i_ex%0.4i' %
                                    (nb_categories, nb_exemplars))
            sys.stdout = open(log_file,'w')
            results_o1_gen[i, j], results_o2_gen[i, j] = \
                exectue_fn(nb_categories, nb_exemplars, params)
            sys.stdout = stdout
            # Save results from this run to text file
            save_file_gen = os.path.join(results_path, 'results_o1_gen.npy')
            np.save(save_file_gen, results_o1_gen)
            save_file_gen = os.path.join(results_path, 'results_o2_gen.npy')
            np.save(save_file_gen, results_o2_gen)
    print('Experiment loop complete.')

def main():
    assert args.noise >= 0. and args.noise <= 1.
    print('Using noise level %0.2f' % args.noise)
    # Create the experiment parameter dictionary
    params = {
        'nb_epochs': args.nb_epochs,
        'batch_size': args.batch_size,
        'nb_trials': 10,
        'nb_test': 1000,
        'noise': args.noise
    }
    # Start the experiment loop
    category_trials = [2, 4, 8, 16, 32, 50]
    exemplar_trials = [3, 6, 9, 12, 15, 18]
    experiment_loop(
        run_experiment, category_trials=category_trials,
        exemplar_trials=exemplar_trials, params=params,
        results_path=args.save_path
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-ep', '--nb_epochs',
                        help='The number of epochs to train for.',
                        required=False, type=int)
    parser.add_argument('-sp', '--save_path',
                        help='The file path where results should be saved',
                        required=False, type=str)
    parser.add_argument('-no', '--noise',
                        help='Noise fraction; binomial probability between '
                             '0-1.',
                        required=False, type=float)
    parser.add_argument('-b', '--batch_size',
                        help='Int indicating the batch size to use',
                        required=False, type=int)
    parser.set_defaults(nb_epochs=400)
    parser.set_defaults(save_path='../results/mlp_results_combined')
    parser.set_defaults(noise=0.)
    parser.set_defaults(batch_size=32)
    args = parser.parse_args()
    main()