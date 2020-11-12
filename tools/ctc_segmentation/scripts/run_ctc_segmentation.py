# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wav
import torch
from utils import get_segments, listener_configurer, listener_process, worker_configurer, worker_process

import nemo.collections.asr as nemo_asr

parser = argparse.ArgumentParser(description="CTC Segmentation")
parser.add_argument("--output_dir", default='output', type=str, help='Path to output directory')
parser.add_argument(
    "--data",
    type=str,
    required=True,
    help='Path to directory with audio files and associated transcripts (same respective names only formats are '
    'different or path to wav file (transcript should have the same base name and be located in the same folder'
    'as the wav file.',
)
parser.add_argument('--window_len', type=int, default=8000, help='Window size for ctc segmentation algorithm')
parser.add_argument('--no_parallel', action='store_true', help='Flag to disable parallel segmentation')
parser.add_argument('--sample_rate', type=int, default=16000, help='Sampling rate')
parser.add_argument(
    '--model', type=str, default='QuartzNet15x5Base-En', help='Path to model checkpoint or pre-trained model name',
)
parser.add_argument('--debug', action='store_true', help='Flag to enable debugging messages')

logger = logging.getLogger('ctc_segmentation')  # use module name

if __name__ == '__main__':

    args = parser.parse_args()

    # setup logger
    log_dir = os.path.join(args.output_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'ctc_segmentation_{args.window_len}.log')
    level = 'DEBUG' if args.debug else 'INFO'
    if True:  # args.no_parallel:
        logger = logging.getLogger('CTC')
        file_handler = logging.FileHandler(filename=log_file)
        stdout_handler = logging.StreamHandler(sys.stdout)
        handlers = [file_handler, stdout_handler]
        logging.basicConfig(handlers=handlers, level=level)

    if os.path.exists(args.model):
        asr_model = nemo_asr.models.EncDecCTCModel.restore_from(args.model)
    elif args.model in nemo_asr.models.EncDecCTCModel.get_available_model_names():
        asr_model = nemo_asr.models.EncDecCTCModel.from_pretrained(args.model, strict=False)
    else:
        raise ValueError(
            f'{args.model} not a valid model name or path. Provide path to the pre-trained checkpoint '
            f'or choose from {nemo_asr.models.EncDecCTCModel.list_available_models()}'
        )

    # extract ASR vocabulary and add blank symbol
    vocabulary = asr_model.cfg.decoder['params']['vocabulary']
    odim = len(asr_model._cfg.decoder.params['vocabulary']) + 1
    logging.debug(f'ASR Model vocabulary: {vocabulary}')

    # add blank to vocab
    vocabulary = ["ε"] + list(vocabulary)
    data = Path(args.data)
    output_dir = Path(args.output_dir)

    if os.path.isdir(data):
        audio_paths = data.glob("*.wav")
        data_dir = data
    else:
        audio_paths = [Path(data)]
        data_dir = Path(os.path.dirname(data))

    all_log_probs = []
    all_transcript_file = []
    all_segment_file = []
    all_wav_paths = []
    segments_dir = os.path.join(args.output_dir, 'segments')
    os.makedirs(segments_dir, exist_ok=True)
    for path_audio in audio_paths:
        transcript_file = os.path.join(data_dir, path_audio.name.replace(".wav", ".txt"))
        segment_file = os.path.join(
            segments_dir, f"{args.window_len}_" + path_audio.name.replace(".wav", "_segments.txt")
        )
        try:
            sample_rate, signal = wav.read(path_audio)
            if sample_rate != args.sample_rate:
                raise ValueError(
                    f'Sampling rate of the audio file {path_audio} doesn\'t match ' f'--sample_rate={args.sample_rate}'
                )
        except ValueError:
            logging.error(
                f"{path_audio} should be a .wav mono file with the sampling rate used for the ASR model training"
                f"specified with {args.sample_rate}."
            )
            raise

        original_duration = len(signal) / sample_rate
        logging.debug(f'Duration: {original_duration}s, file_name: {path_audio}')
        log_probs = asr_model.transcribe(paths2audio_files=[str(path_audio)], batch_size=1, logprobs=True)[0].cpu()

        transcript_default = "a carrier's dog by percy j billinghurst this is a libevox recording all libervoch's recordings are in the public domain for more information or to volunteer please visit libervoch stopborg a carrier on his way to a market town had occasion to stop at some houses by the roadside in the way of his business leaving his cart and horse upon the public road under the protection of a passenger and a trusty dog upon his return he missed a lead horse belonging to a gentleman in the neighbourhood which he had tied to the end of the cart and likewise one of the female passengers on inquiry he was informed that during his absence the female who had been anxious to try the medal of the pony had mounted it and that the animal had set off at full speed the carrier expressed much anxiety for the safety of the young woman casting at the same time an expressive look at his dog oscar observed his master's eye and aware of its meaning instantly set off in pursuit of the pony which coming up with soon after he made a sudden spring seized the britdle and held the animal fast several people having observed the circumstance and the perilous situation of the girl came to relieve her oscar however notwithstanding their repeated endeavours would not quit his hold and the pony was actually led into the stable with the dog till such time as the carrier should arrive upon the carrier entering the stable oscar wagged his tail in token of satisfaction and immediately relinquished the brittle to his master end of a carrier's dog by percy j billinghurst"

        transcript = asr_model.transcribe(paths2audio_files=[str(path_audio)], batch_size=1)[0]
        # remove
        print(path_audio)
        print(original_duration)
        print(f'------> {signal[:10]} {sum(signal[:10] == [ 0,  0,  0, -1,  0,  0,  0,  0,  1,  1]) == 10}')
        print(f'-----> {torch.norm(log_probs)} {torch.norm(log_probs) == 8646.0586}')
        greedy_predictions = log_probs.argmax(dim=-1, keepdim=False)
        print(f'Transcript is the same: {transcript == transcript_default}')
        print(f' Sum greedy prediction: {sum(greedy_predictions) == 120993}')
        import pickle
        pickle.dump(greedy_predictions, open(os.path.join(args.output_dir, 'greeedy_predictions.p'), 'wb'))
        import pdb; pdb.set_trace()
        # move blank values to the first column
        log_probs = np.squeeze(log_probs, axis=0)
        blank_col = log_probs[:, -1].reshape((log_probs.shape[0], 1))
        log_probs = np.concatenate((blank_col, log_probs[:, :-1]), axis=1)
        all_log_probs.append(log_probs)
        all_segment_file.append(str(segment_file))
        all_transcript_file.append(str(transcript_file))
        all_wav_paths.append(path_audio)

    del asr_model
    torch.cuda.empty_cache()

    start_time = time.time()
    if args.no_parallel:
        for i in range(len(all_log_probs)):
            get_segments(
                all_log_probs[i],
                all_wav_paths[i],
                all_transcript_file[i],
                all_segment_file[i],
                vocabulary,
                args.window_len,
            )
    else:
        queue = multiprocessing.Queue(-1)

        listener = multiprocessing.Process(target=listener_process, args=(queue, listener_configurer, log_file, level))
        listener.start()
        workers = []
        for i in range(len(all_log_probs)):
            worker = multiprocessing.Process(
                target=worker_process,
                args=(
                    queue,
                    worker_configurer,
                    level,
                    all_log_probs[i],
                    all_wav_paths[i],
                    all_transcript_file[i],
                    all_segment_file[i],
                    vocabulary,
                    args.window_len,
                ),
            )
            workers.append(worker)
            worker.start()
        for w in workers:
            w.join()
        queue.put_nowait(None)
        listener.join()

    total_time = time.time() - start_time
    logger.info(f'Total execution time: ~{round(total_time/60)}min')
    logger.info(f'Saving logs to {log_file}')

    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            lines = f.readlines()