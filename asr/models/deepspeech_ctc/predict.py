#!python
import sys
import argparse
from pathlib import Path

import numpy as np
import torch

from asr.utils.dataset import PredictDataset
from asr.utils.dataloader import PredictDataLoader
from asr.utils.logger import logger, set_logfile
from asr.utils.misc import onehot2int, remove_duplicates
from asr.utils import params as p

from asr.kaldi.latgen import LatGenCTCDecoder

from ..trainer import FRAME_REDUCE_FACTOR
from .network import *


class Predictor:

    def __init__(self, use_cuda=False, continue_from=None, verbose=False, *args, **kwargs):
        self.use_cuda = use_cuda
        self.verbose = verbose

        # load from args
        assert continue_from is not None
        self.model = DeepSpeech(num_classes=p.NUM_CTC_LABELS)
        if self.use_cuda:
            self.model.cuda()
        self.load(continue_from)

        # prepare kaldi latgen decoder
        self.decoder = LatGenCTCDecoder()

    def decode(self, data_loader):
        self.model.eval()
        with torch.no_grad():
            for i, (data) in enumerate(data_loader):
                # predict phones using AM
                xs, frame_lens, filenames = data
                if self.use_cuda:
                    xs = xs.cuda()
                ys_hat = self.model(xs)
                frame_lens = torch.ceil(frame_lens.float() / FRAME_REDUCE_FACTOR).int()
                # decode using Kaldi's latgen decoder
                # no need to normalize posteriors with state priors when we use CTC
                # https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/43908.pdf
                loglikes = torch.log(ys_hat)
                if self.use_cuda:
                    loglikes = loglikes.cpu()
                words, alignment, w_sizes, a_sizes = self.decoder(loglikes, frame_lens)
                # print results
                loglikes = [l[:s] for l, s in zip(loglikes, frame_lens)]
                words = [w[:s] for w, s in zip(words, w_sizes)]
                for results in zip(filenames, loglikes, words):
                    self.print_result(*results)

    def print_result(self, filename, loglikes, words):
        logger.info(f"decoding wav file: {str(Path(filename).resolve())}")
        if self.verbose:
            labels = onehot2int(loglikes).squeeze()
            logger.info(f"labels: {' '.join([str(x) for x in labels.tolist()])}")
            symbols = [self.decoder.labeler.idx2phone(x) for x in remove_duplicates(labels, blank=0)]
            logger.info(f"symbols: {' '.join(symbols)}")
        words = words.squeeze()
        text = ' '.join([self.decoder.labeler.idx2word(i) for i in words]) \
               if words.dim() else '<null output from decoder>'
        logger.info(f"decoded text: {text}")

    def load(self, file_path):
        if isinstance(file_path, str):
            file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"no such file {file_path} exists")
            sys.exit(1)
        logger.info(f"loading the model from {file_path}")
        if not self.use_cuda:
            states = torch.load(file_path, map_location='cpu')
        else:
            states = torch.load(file_path, map_location='cuda:0')
        self.model.load_state_dict(states["model"])


def predict(argv):
    parser = argparse.ArgumentParser(description="DeepSpeech prediction")
    parser.add_argument('--verbose', default=False, action='store_true', help="set true if you need to check AM output")
    parser.add_argument('--use-cuda', default=False, action='store_true', help="use cuda")
    parser.add_argument('--batch-size', default=8, type=int, help="number of simultaneous decoding")
    parser.add_argument('--log-dir', default='./logs_deepspeech_ctc', type=str, help="filename for logging the outputs")
    parser.add_argument('--continue-from', type=str, help="model file path to make continued from")
    parser.add_argument('wav_files', type=str, nargs='+', help="list of wav_files for prediction")
    args = parser.parse_args(argv)

    set_logfile(Path(args.log_dir, "predict.log"))
    version_log(args)

    if args.continue_from is None:
        logger.error("model name is missing: add '--continue-from <model-name>' in options")
        #parser.print_help()
        sys.exit(1)

    predictor = Predictor(**vars(args))

    dataset = PredictDataset(args.wav_files)
    data_loader = PredictDataLoader(dataset=dataset, batch_size=args.batch_size,
                                    pin_memory=args.use_cuda)

    # run prediction
    predictor.decode(data_loader)


if __name__ == "__main__":
    pass