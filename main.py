import argparse
import os
import numpy as np
from mido import MidiFile
from keras.models import load_model
from sklearn.model_selection import train_test_split

import file_utility
import midi_utility
import model_utility
import utility
import plotter

np.set_printoptions(threshold=np.inf)

parser = argparse.ArgumentParser()
parser.add_argument('--prepare-midi', action='store_true',
                    help='validates, quantizes and saves train and test midi data')
parser.add_argument('--prepare-predictions', action='store_true',
                    help='validates, quantizes and saves prediction midi data')
parser.add_argument('--include-baseline', action='store_true',
                    help='also outputs a midi file with static velocities')
parser.add_argument('--batch-size', default=4, type=int, help='batch size')
parser.add_argument('--epochs', default=1, type=int, help='batch size')
parser.add_argument(
    '--predict', help='make prediction for midi file on given path')
parser.add_argument(
    '--plot', help='make prediction and plot it compared with known answer')
parser.add_argument('-l', '--load-model', action='store_true',
                    help='loads a model from disk')
parser.add_argument('-t', '--train-model', action='store_true',
                    help='trains the model for the set number of epochs')


def load_data():
    '''Loads the musical performances and returns sets of inputs and labels
    (notes and resulting velocities), one for testing and one for training.'''

    print('Loading data ...')

    midi_data_inputs_path = './midi_data_valid_quantized_inputs'
    midi_data_velocities_path = './midi_data_valid_quantized_velocities'

    def maybe_add_name(filename):
        return filename[1] if filename[1].split('.')[-1] == 'npy' else None

    names = utility.map_removing_none(
        maybe_add_name, enumerate(os.listdir(midi_data_inputs_path)))

    # N songs of Mn timesteps, each with:
    #   - 176 (= 88 * 2) pitch classes
    #   - 1 stress of beat (strong/weak)
    #   - 2 number of notes played and sustained
    #   - 1 time progression
    #
    # Iow, each data point: [Mn, 180]
    input_data = []

    # N songs of Mn timesteps, each with 88 velocities.
    # Iow, each data point: [Mn, 88]
    velocity_data = []

    for i, filename in enumerate(names):
        abs_inputs_path = os.path.join(midi_data_inputs_path, filename)
        abs_velocities_path = os.path.join(midi_data_velocities_path, filename)
        loaded_inputs = np.load(abs_inputs_path)

        input_data.append(loaded_inputs)

        loaded_velocities = np.load(abs_velocities_path)
        loaded_velocities = loaded_velocities / 127  # MIDI velocity -> float.

        velocity_data.append(loaded_velocities)

        assert input_data[i].shape[0] == velocity_data[i].shape[0]

    return train_test_split(input_data, velocity_data, test_size=0.05, random_state=1988)


args = parser.parse_args()

midi_data_path = './midi_data'
midi_data_valid_path = './midi_data_valid'
midi_data_valid_quantized_path = './midi_data_valid_quantized'

predictables_path = './input'
predictables_valid_path = './input_valid'

data_path = './data'
training_set_path = os.path.join(data_path, 'training')
velocities_path = os.path.join(data_path, 'velocities')
validation_set_path = os.path.join(data_path, 'validation')

quantization = 4

if args.prepare_midi:
    print('Preparing train and test MIDI data ...')

    file_utility.validate_data(midi_data_path, quantization)
    file_utility.quantize_data(midi_data_valid_path, quantization)
    file_utility.save_data(midi_data_valid_quantized_path, quantization)

if args.prepare_predictions:
    print('Preparing prediction MIDI data ...')

    file_utility.validate_data(predictables_path, quantization)
    file_utility.save_data(predictables_valid_path, quantization)

x_train, x_test, y_train, y_test = load_data()

x_train = np.array(x_train)
y_train = np.array(y_train)
x_test = np.array(x_test)
y_test = np.array(y_test)

print('Train sequences: {}'.format(len(x_train)))
print('Test sequences: {}'.format(len(x_test)))

model_name = 'model'
model_path = 'models/' + model_name + '.h5'
history_path = 'models/history'

if args.load_model:
    print('Loading model ...')
    model = load_model(model_path)
else:
    model = model_utility.create_model(batch_size=args.batch_size)

if args.train_model:
    model, history = model_utility.train_model(model,
                                               x_train=x_train,
                                               y_train=y_train,
                                               x_test=x_test,
                                               y_test=y_test,
                                               batch_size=args.batch_size,
                                               epochs=args.epochs,
                                               model_path=model_path,
                                               save_model=True)

    # Take metrics and add them to the existing history (iff we loaded the
    # model, iow if we have trained the model before) or use it as a new
    # history, saving the result.
    if args.load_model:
        new_history = np.load('{}.npy'.format(history_path)).item()
        metrics = ['val_loss', 'val_mean_squared_error',
                   'loss', 'mean_squared_error']
        for metric in metrics:
            new_history[metric] = np.concatenate(
                [new_history[metric], history.history[metric]])
    else:
        new_history = history.history
    np.save(history_path, new_history)

    # Plot history.
    plotter.plot_model_history(new_history, model_name)

# Evaluate iff we have a model that has at some point been trained.
if args.load_model or args.train_model:
    loss_and_metrics = model_utility.evaluate(
        model, x_test, y_test, batch_size=args.batch_size)
    print('Final loss and metrics:', loss_and_metrics)


if args.predict:
    prediction_data_path = os.path.join(
        './input_valid_inputs', args.predict + '.npy')
    prediction_midi_path = os.path.join('./input', args.predict)

    prediction = model_utility.predict(
        model, path=prediction_data_path, batch_size=args.batch_size)
    prediction = np.clip(prediction / 127.0, 0, 1)

    plotter.plot_prediction(args.predict, model=model,
                            batch_size=args.batch_size)

    print('Stylifying MIDI file ...')

    midi_file = MidiFile(prediction_midi_path)
    stylified_midi_file = midi_utility.stylify(
        midi_file, prediction, quantization)

    stylified_midi_file.save(os.path.join('./output', args.predict))

if args.include_baseline and args.predict:
    input_data_path = os.path.join(
        './input_valid_inputs', args.predict + '.npy')
    input_midi_path = os.path.join('./input', args.predict)

    print('Creating baseline (all velocities set to 64) MIDI file ...')

    input_data = np.load(input_data_path)
    input_length = input_data.shape[0]
    output_size = input_data.shape[1] // 2
    velocities = np.full((input_length, output_size), 0.5)  # Velocity of 63.5.

    midi_file = MidiFile(input_midi_path)
    baseline_midi_file = midi_utility.stylify(
        midi_file, velocities, quantization)

    baseline_midi_file.save(os.path.join('./output_baseline', args.predict))

if args.plot:
    plotter.plot_comparison(args.plot, model=model, batch_size=args.batch_size)
