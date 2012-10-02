#!/usr/bin/env python
"""
Morfessor 2.0 - Python implementation of the Morfessor method
"""

__all__ = ['InputFormatError', 'MorfessorIO', 'Lexicon', 'BaselineModel',
           'Annotations']

__version__ = '2.0.0pre1'
__author__ = 'Sami Virpioja, Peter Smit'
__author_email__ = "sami.virpioja@aalto.fi"

import codecs
import collections
import datetime
import gzip
import itertools
import locale
import logging
import math
import random
import re
import sys
import time
import types

try:
    # In Python2 import cPickle for better performance
    import cPickle as pickle
except ImportError:
    import pickle

try:
    from functools import reduce
except ImportError:
    pass

_logger = logging.getLogger(__name__)

show_progress_bar = True


def _progress(iter_func):
    """Decorator/function for displaying a progress bar when iterating
    through a list.

    iter_func can be both a function providing a iterator (for decorator
    style use) or an iterator itself.

    No progressbar is displayed when stderr is redirected to a file

    If the progressbar module is available a fancy percentage style
    progressbar is displayed. Otherwise 20 dots are printed as indicator.

    """

    if not show_progress_bar:
        return iter_func

    #Try to see or the progressbar module is available, else fabricate our own
    try:
        from progressbar import ProgressBar
    except ImportError:
        class SimpleProgressBar:
            NUM_DOTS = 60

            def __call__(self, it):
                self.it = iter(it)
                self.i = 0

                # Dot frequency is determined as ceil(len(it) / NUM_DOTS)
                self.dotfreq = len(it) + self.NUM_DOTS - 1 // self.NUM_DOTS
                if self.dotfreq < 1:
                    self.dotfreq = 1

                return self

            def __iter__(self):
                return self

            def next(self):
                self.i += 1
                if self.i % self.dotfreq == 0:
                    sys.stderr.write('.')
                try:
                    return self.it.next()
                except StopIteration:
                    sys.stderr.write('\n')
                    raise
        ProgressBar = SimpleProgressBar

    # In case of a decorator (argument is a function),
    # wrap the functions result is a ProgressBar and return the new function
    if isinstance(iter_func, types.FunctionType):
        def i(*args, **kwargs):
            if logging.getLogger(__name__).isEnabledFor(logging.INFO):
                return ProgressBar()(iter_func(*args, **kwargs))
            else:
                return iter_func(*args, **kwargs)
        return i

    #In case of an iterator, wrap it in a ProgressBar and return it.
    elif hasattr(iter_func, '__iter__'):
        return ProgressBar()(iter_func)

    #If all else fails, just return the original.
    return iter_func


def _constructions_to_str(constructions):
    """Return a readable string for a list of constructions."""
    if (isinstance(constructions[0], str) or
            isinstance(constructions[0], unicode)):
        # Constructions are strings
        return ' + '.join(constructions)
    else:
        # Constructions are not strings (should be tuples of strings)
        return ' + '.join(map(lambda x: ' '.join(x), constructions))


class Error(Exception):
    """Base class for exceptions in this module."""
    pass


class MorfessorIO:
    """Definition for all input and output files. Also handles all
    encoding issues.

    """

    def __init__(self, encoding=None, construction_separator=' + ',
                 comment_start='#', compound_separator='\W+',
                 atom_separator=None):
        self.encoding = encoding
        self.construction_separator = construction_separator
        self.comment_start = comment_start
        self.compound_separator = compound_separator
        self.atom_separator = atom_separator
        if atom_separator is not None:
            self._atom_sep_re = re.compile(atom_separator, re.UNICODE)

    def read_segmentation_file(self, file_name, **kwargs):
        """Read segmentation file.

        File format:
        <count> <construction1><sep><construction2><sep>...<constructionN>

        """
        _logger.info("Reading segmentations from '%s'..." % file_name)
        for line in self._read_text_file(file_name):
            count, compound = line.split(' ', 1)
            yield int(count), compound.split(self.construction_separator)
        _logger.info("Done.")

    def write_segmentation_file(self, file_name, segmentations, **kwargs):
        """Write segmentation file.

        File format:
        <count> <construction1><sep><construction2><sep>...<constructionN>

        """
        _logger.info("Saving segmentations to '%s'..." % file_name)
        with self._open_text_file_write(file_name) as file_obj:
            d = datetime.datetime.now().replace(microsecond=0)
            file_obj.write("# Output from Morfessor Baseline %s, %s\n" %
                           (__version__, d))
            for count, segmentation in segmentations:
                if self.atom_separator is None:
                    s = self.construction_separator.join(segmentation)
                else:
                    s = self.construction_separator.join(
                        map(lambda x: ' '.join(x), segmentation))
                file_obj.write("%d %s\n" % (count, s))
        _logger.info("Done.")

    def read_corpus_files(self, file_names):
        """Read one or more corpus files.

        Yield for each compound found (1, compound, compound_atoms).

        """
        for file_name in file_names:
            for item in self.read_corpus_file(file_name):
                yield item

    def read_corpus_file(self, file_name):
        """Read one corpus file.

        Yield for each compound found (1, compound, compound_atoms).

        """
        _logger.info("Reading corpus from '%s'..." % file_name)
        compound_sep = re.compile(self.compound_separator, re.UNICODE)
        for line in self._read_text_file(file_name):
            for compound in compound_sep.split(line):
                if len(compound) > 0:
                    yield 1, compound, self._split_atoms(compound)
        _logger.info("Done.")

    def read_corpus_list_file(self, file_name):
        """Read a corpus list file.

        Each line has the format:
        <count> <compound>

        Yield tuples (count, compound, compound_atoms) for each compound.

        """
        _logger.info("Reading corpus from list '%s'..." % file_name)
        for line in self._read_text_file(file_name):
            try:
                count, compound = line.split(None, 1)
                yield int(count), compound, self._split_atoms(compound)
            except ValueError:
                yield 1, line, self._split_atoms(line)
        _logger.info("Done.")

    def read_annotations_file(self, file_name):
        """Read a annotations file.

        Each line has the format:
        <compound> <constr1> <constr2>... <constrN>, <constr1>...<constrN>, ...

        Yield tuples (compound, list(analyses)).

        """
        _logger.info("Reading annotations from '%s'..." % file_name)
        for line in self._read_text_file(file_name):
            analyses = []
            compound, analyses_line = line.split(None, 1)

            for analysis in analyses_line.split(','):
                analyses.append(analysis.split(' '))

            yield compound, analyses
        _logger.info("Done.")

    def write_lexicon_file(self, file_name, lexicon):
        """Write to a Lexicon file all constructions and their counts."""
        _logger.info("Saving model lexicon to '%s'..." % file_name)
        with self._open_text_file_write(file_name) as file_obj:
            for construction, count in lexicon:
                file_obj.write("%d %s\n" % (count, construction))
        _logger.info("Done.")

    def read_binary_model_file(self, file_name):
        """Read a pickled model from file."""
        _logger.info("Loading model from '%s'..." % file_name)
        with open(file_name, 'rb') as fobj:
            model = pickle.load(fobj)
        _logger.info("Done.")
        return model

    def write_binary_model_file(self, file_name, model):
        """Pickle a model to a file."""
        _logger.info("Saving model to '%s'..." % file_name)
        with open(file_name, 'wb') as fobj:
            pickle.dump(model, fobj, pickle.HIGHEST_PROTOCOL)
        _logger.info("Done.")

    def _split_atoms(self, construction):
        """Split construction to its atoms."""
        if self.atom_separator is None:
            return construction
        else:
            return self._atom_sep_re.split(construction)

    def _open_text_file_write(self, file_name):
        """Open a file with the appropriate compression and encoding"""
        if file_name == '-':
            file_obj = sys.stdout
        elif file_name.endswith('.gz'):
            file_obj = gzip.open(file_name, 'wb')
        else:
            file_obj = open(file_name, 'wb')
        if self.encoding is None:
            # Take encoding from locale if not set so far
            self.encoding = locale.getpreferredencoding()
        return codecs.getwriter(self.encoding)(file_obj)

    def _read_text_file(self, file_name):
        """Read a text file with the appropriate compression and encoding.

        Comments and empty lines are skipped.

        """
        if self.encoding is None:
            self.encoding = self._find_encoding(file_name)
        if file_name == '-':
            file_obj = sys.stdin
        elif file_name.endswith('.gz'):
            file_obj = gzip.open(file_name, 'rb')
        else:
            file_obj = open(file_name, 'rb')

        for line in codecs.getreader(self.encoding)(file_obj):
            line = line.rstrip()
            if len(line) > 0 and not line.startswith(self.comment_start):
                yield line

    def _find_encoding(self, *files):
        """Test default encodings on reading files.

        If no encoding is given, this method can be used to test which
        of the default encodings would work.

        """
        test_encodings = [locale.getpreferredencoding(), 'utf-8']
        for encoding in test_encodings:
            ok = True
            for f in files:
                if f == '-':
                    continue
                try:
                    if f.endswith('.gz'):
                        file_obj = gzip.open(f, 'rb')
                    else:
                        file_obj = open(f, 'rb')

                    for _ in codecs.getreader(encoding)(file_obj):
                        pass
                except UnicodeDecodeError:
                    ok = False
                    break
            if ok:
                _logger.info("Detected %s encoding" % encoding)
                return encoding

        raise UnicodeError("Can not determine encoding of input files")

# rcount = root count (from corpus)
# count = total count of the node
# splitloc = list of location of the possible splits for virtual
#            constructions; empty if real construction
ConstrNode = collections.namedtuple('ConstrNode',
                                    ['rcount', 'count', 'splitloc'])


class BaselineModel:
    """Morfessor Baseline model class."""

    def __init__(self, forcesplit_list=None, corpusweight=1.0,
                 use_skips=False):
        """Initialize a new model instance.

        Arguments:
            forcesplit_list -- force segmentations on the characters in
                               the given list
            corpusweight -- weight for the corpus cost
            use_skips -- randomly skip frequently occurring constructions
                         to speed up training

        """
        self.analyses = {}

        # Cost variables
        self.lexicon_coding = LexiconEncoding()
        self.corpus_coding = CorpusEncoding(self.lexicon_coding,
                                            corpusweight)
        self.annot_coding = None

        # Configuration variables
        self.use_skips = use_skips  # Random skips for frequent constructions
        self.supervised = False

        self.counter = collections.Counter()  # Counter for random skipping
        if forcesplit_list is None:
            self.forcesplit_list = []
        else:
            self.forcesplit_list = forcesplit_list

        self.penalty = -9999.9

    def _get_compounds(self):
        """Return the compound types stored by the model."""
        return [w for w, node in self.analyses.items()
                if node.rcount > 0]

    def get_constructions(self):
        """Return a list of the present constructions and their counts."""
        return sorted((c, node.count) for c, node in self.analyses.items()
                      if len(node.splitloc) == 0)

    def _add_compound(self, compound, c):
        """Add compound with count c to data."""
        self.corpus_coding.boundaries += c
        self._modify_construction_count(compound, c)
        oldrc = self.analyses[compound].rcount
        self.analyses[compound] = \
            self.analyses[compound]._replace(rcount=oldrc + c)

    def _remove(self, construction):
        """Remove construction from model."""
        rcount, count, splitloc = self.analyses[construction]
        self._modify_construction_count(construction, -count)
        return rcount, count

    def _expand_construction(self, construction):
        """Expand a virtual construction to its parts."""
        rcount, count, splitloc = self.analyses[construction]
        constructions = []
        if len(splitloc) > 0:
            for child in self._splitloc_to_segmentation(construction,
                                                        splitloc):
                constructions += self._expand_construction(child)
        else:
            constructions.append(construction)
        return constructions

    def _random_split(self, compound, threshold):
        """Return a random split for compound.

        Arguments:
            compound -- compound to split
            threshold -- probability of splitting at each position

        """
        splitloc = [i for i in range(1, len(compound))
                    if random.random() < threshold]
        return self._splitloc_to_segmentation(compound, splitloc)

    def _set_compound_analysis(self, compound, parts, ptype='flat'):
        """Set analysis of compound to according to given segmentation.

        Arguments:
            compound -- compound to split
            parts -- desired constructions of the compound
            ptype -- type of the parse tree to use

        If ptype is 'rbranch', the analysis is stored internally as a
        right-branching tree. If ptype is 'flat', the analysis is stored
        directly to the compound's node.

        """
        if len(parts) == 1:
            rcount, count = self._remove(compound)
            self.analyses[compound] = ConstrNode(rcount, 0, [])
            self._modify_construction_count(compound, count)
        elif ptype == 'flat':
            rcount, count = self._remove(compound)
            splitloc = self._segmentation_to_splitloc(parts)
            self.analyses[compound] = ConstrNode(rcount, count, splitloc)
            for constr in parts:
                self._modify_construction_count(constr, count)
        elif ptype == 'rbranch':
            construction = compound
            for p in range(len(parts)):
                rcount, count = self._remove(construction)
                prefix = parts[p]
                if p == len(parts) - 1:
                    self.analyses[construction] = ConstrNode(rcount, 0, [])
                    self._modify_construction_count(construction, count)
                else:
                    suffix = reduce(lambda x, y: x + y, parts[p + 1:])
                    self.analyses[construction] = ConstrNode(rcount, count,
                                                             [len(prefix)])
                    self._modify_construction_count(prefix, count)
                    self._modify_construction_count(suffix, count)
                    construction = suffix
        else:
            raise Error("Unknown parse type '%s'" % ptype)

    def _update_annotation_choices(self):
        """Update the selection of alternative analyses in annotations.

        For semi-supervised models, select the most likely alternative
        analyses included in the annotations of the compounds.

        """
        if not self.supervised:
            return

        # Add data to self.annotatedconstructions
        constructions = collections.Counter()
        for w, alternatives in self.annotations.get_data():
            analysis, cost = self._best_analysis(alternatives)

            for m in analysis:
                constructions[m] += self.analyses[w].rcount

        self.annot_coding.set_constructions(constructions)

        for m, f in constructions.items():
            count = 0
            if m in self.analyses and len(self.analyses[m].splitloc) == 0:
                count = self.analyses[m].count
            self.annot_coding.update_count(m, -1, count)

    def _best_analysis(self, choices):
        """Select the best analysis out of the given choices."""
        bestcost = None
        bestanalysis = None
        for analysis in choices:
            cost = 0.0
            for m in analysis:
                if m in self.analyses and len(self.analyses[m].splitloc) == 0:
                    cost += (math.log(self.corpus_coding.tokens) -
                             math.log(self.analyses[m].count))
                else:
                    cost -= self.penalty  # penaltylogprob is
                    # negative
            if bestcost is None or cost < bestcost:
                bestcost = cost
                bestanalysis = analysis
        return bestanalysis, bestcost

    def _force_split(self, compound):
        """Return forced split of the compound."""
        if len(self.forcesplit_list) == 0:
            return [compound]
        clen = len(compound)
        j = 0
        parts = []
        for i in range(1, clen):
            if compound[i] in self.forcesplit_list:
                parts.append(compound[j:i])
                parts.append(compound[i:i + 1])
                j = i + 1
        if j < clen:
            parts.append(compound[j:])
        return parts

    def _test_skip(self, construction):
        """Return true if construction should be skipped."""
        if construction in self.counter:
            t = self.counter[construction]
            if random.random() > 1.0 / max(1, t):
                return True
        self.counter[construction] += 1
        return False

    def _viterbi_optimize(self, compound, addcount=0, maxlen=30):
        """Optimize segmentation of the compound using the Viterbi algorithm.

        Arguments:
          compound -- compound to optimize
          addcount -- constant for additive smoothing of Viterbi probs
          maxlen -- maximum length for a construction

        Returns list of segments.

        """
        clen = len(compound)
        if clen == 1:  # Single atom
            return [compound]
        if self.use_skips and self._test_skip(compound):
            return self._expand_construction(compound)
        # Collect forced subsegments
        parts = self._force_split(compound)
        # Use Viterbi algorithm to optimize the subsegments
        constructions = []
        for part in parts:
            constructions += self.viterbi_segment(part, addcount=addcount,
                                                  maxlen=maxlen)[0]
        self._set_compound_analysis(compound, constructions)
        return constructions

    def _recursive_optimize(self, compound):
        """Optimize segmentation of the compound using recursive splitting.

        Returns list of segments.

        """
        if len(compound) == 1:  # Single atom
            return [compound]
        if self.use_skips and self._test_skip(compound):
            return self._expand_construction(compound)
        # Collect forced subsegments
        parts = self._force_split(compound)
        if len(parts) == 1:
            # just one part
            return self._recursive_split(compound)
        self._set_compound_analysis(compound, parts)
        # Use recursive algorithm to optimize the subsegments
        constructions = []
        for part in parts:
            constructions += self._recursive_split(part)
        return constructions

    def _recursive_split(self, construction):
        """Optimize segmentation of the construction by recursive splitting.

        Returns list of segments.

        """
        if len(construction) == 1:  # Single atom
            return [construction]
        if self.use_skips and self._test_skip(construction):
            return self._expand_construction(construction)
        rcount, count = self._remove(construction)

        # Check all binary splits and no split
        self._modify_construction_count(construction, count)
        mincost = self.get_cost()
        self._modify_construction_count(construction, -count)
        splitloc = []
        for i in range(1, len(construction)):
            prefix = construction[:i]
            suffix = construction[i:]
            self._modify_construction_count(prefix, count)
            self._modify_construction_count(suffix, count)
            cost = self.get_cost()
            self._modify_construction_count(prefix, -count)
            self._modify_construction_count(suffix, -count)
            if cost <= mincost:
                mincost = cost
                splitloc = [i]

        if len(splitloc) > 0:
            # Virtual construction
            self.analyses[construction] = ConstrNode(rcount, count, splitloc)
            prefix = construction[:splitloc[0]]
            suffix = construction[splitloc[0]:]
            self._modify_construction_count(prefix, count)
            self._modify_construction_count(suffix, count)
            lp = self._recursive_split(prefix)
            if suffix != prefix:
                return lp + self._recursive_split(suffix)
            else:
                return lp + lp
        else:
            # Real construction
            self.analyses[construction] = ConstrNode(rcount, 0, [])
            self._modify_construction_count(construction, count)
            return [construction]

    def _modify_construction_count(self, construction, dcount):
        """Modify the count of construction by dcount.

        For virtual constructions, recurses to child nodes in the
        tree. For real constructions, adds/removes construction
        to/from the lexicon whenever necessary.

        """
        if construction in self.analyses:
            rcount, count, splitloc = self.analyses[construction]
        else:
            rcount, count, splitloc = 0, 0, []
        newcount = count + dcount
        if newcount == 0:
            del self.analyses[construction]
        else:
            self.analyses[construction] = ConstrNode(rcount, newcount,
                                                     splitloc)
        if len(splitloc) > 0:
            # Virtual construction
            children = self._splitloc_to_segmentation(construction, splitloc)
            for child in children:
                self._modify_construction_count(child, dcount)
        else:
            # Real construction
            self.corpus_coding.update_count(construction, count, newcount)
            if self.supervised:
                self.annot_coding.update_count(construction, count, newcount)

            if count == 0 and newcount > 0:
                self.lexicon_coding.add(construction)
            elif count > 0 and newcount == 0:
                self.lexicon_coding.remove(construction)

    def _epoch_update(self, epoch_num):
        """Do model updates that are necessary between training epochs.

        The argument is the number of training epochs finished.

        In practice, this does two things:
        - If random skipping is in use, reset construction counters.
        - If semi-supervised learning is in use and there are alternative
          analyses in the annotated data, select the annotations that are
          most likely given the model parameters. If not hand-set, update
          the weight of the annotated corpus.

        This method should also be run prior to training (with the
        epoch number argument as 0).

        """
        if self.use_skips:
            self.counter = collections.Counter()
        if self.supervised:
            self._update_annotation_choices()
            self.annot_coding.update_weight()

    @staticmethod
    def _segmentation_to_splitloc(constructions):
        """Return a list of split locations for a segmented compound."""
        splitloc = []
        i = 0
        for c in constructions:
            i += len(c)
            splitloc.append(i)
        return splitloc[:-1]

    @staticmethod
    def _splitloc_to_segmentation(compound, splitloc):
        """Return segmentation corresponding to the list of split locations."""
        parts = []
        startpos = 0
        endpos = 0
        for i in range(len(splitloc)):
            endpos = splitloc[i]
            parts.append(compound[startpos:endpos])
            startpos = endpos
        parts.append(compound[endpos:])
        return parts

    def get_cost(self):
        """Return current model cost."""
        cost = (self.corpus_coding.get_cost() +
                self.lexicon_coding.get_cost())
        if self.supervised:
            return cost + self.annot_coding.get_cost()
        else:
            return cost

    def get_segmentations(self):
        """Retrieve segmentations for all compounds encoded by the model."""
        for w in sorted(self.analyses.keys()):
            c = self.analyses[w].rcount
            if c > 0:
                yield c, self._expand_construction(w)

    def load_data(self, corpus, freqthreshold=1, cfunc=lambda x: x,
                  init_rand_split=None):
        """Load data to initialize the model for batch training.

        Arguments:
            corpus -- corpus instance
            freqthreshold -- discard compounds that occur less than
                             given times in the corpus (default 1)
            cfunc -- function (int -> int) for modifying the counts
                     (defaults to identity function)
            init_rand_split -- If given, random split the word with
                               init_rand_split as the probability for each
                               split

        Adds the compounds in the corpus to the model lexicon. Returns
        the total cost.

        """
        for count, _, atoms in corpus:
            if count < freqthreshold:
                continue
            self._add_compound(atoms, cfunc(count))

            if init_rand_split is not None and init_rand_split > 0:
                parts = self._random_split(atoms, init_rand_split)
                self._set_compound_analysis(atoms, parts)

        return self.get_cost()

    def load_segmentations(self, segmentations):
        """Load model from existing segmentations.

        The argument should be an iterator providing a count and a
        segmentation.

        """
        for count, segmentation in segmentations:
            comp = "".join(segmentation)
            self._add_compound(comp, count)
            self._set_compound_analysis(comp, segmentation)

    def set_annotations(self, annotations, annotatedcorpusweight):
        """Prepare model for semi-supervised learning with given
         annotations.

         """
        self.supervised = True
        self.annotations = annotations
        self.annot_coding = AnnotatedCorpusEncoding(self.corpus_coding,
                                                    annotated_corpus_weight=
                                                    annotatedcorpusweight)
        self.annot_coding.boundaries = self.annotations.get_types()

    def train_batch(self, algorithm='recursive', algorithm_params=(),
                    devel_annotations=None, finish_threshold=0.005):
        self._epoch_update(0)
        oldcost = 0.0
        newcost = self.get_cost()
        compounds = list(self._get_compounds())
        _logger.info("Compounds in training data: %s types / %s tokens" %
                     (len(compounds), self.corpus_coding.boundaries))
        epochs = 0
        _logger.info("Starting batch training")
        _logger.info("Epochs: %s\tCost: %s" % (epochs, newcost))
        forced_epochs = 1  # force this many epochs before stopping
        while True:
            # One epoch
            random.shuffle(compounds)

            for w in _progress(compounds):
                if algorithm == 'recursive':
                    segments = self._recursive_optimize(w, *algorithm_params)
                elif algorithm == 'viterbi':
                    segments = self._viterbi_optimize(w, *algorithm_params)
                else:
                    raise Error("unknown algorithm '%s'" % algorithm)
                _logger.debug("#%s -> %s" %
                              (w, _constructions_to_str(segments)))
            epochs += 1

            _logger.debug("Cost before epoch update: %s" % self.get_cost())
            self._epoch_update(epochs)
            oldcost = newcost
            newcost = self.get_cost()

            if devel_annotations is not None:
                # Tune corpus weight based on development data
                tmp = devel_annotations.get_data()
                wlist, annotations = zip(*tmp)
                segments = [self.viterbi_segment(w)[0] for w in wlist]
                d = _estimate_segmentation_dir(segments, annotations)
                if d != 0:
                    if d > 0:
                        self.corpus_coding.weight *= 1 + 2.0 / epochs
                    else:
                        self.corpus_coding.weight *= 1.0 / (1 + 2.0 / epochs)
                    _logger.info("Corpus weight set to %s" %
                                 self.corpus_coding.weight)
                    self._epoch_update(epochs)
                    newcost = self.get_cost()
                    # Prevent stopping on next epoch
                    if forced_epochs < 2:
                        forced_epochs = 2

            _logger.info("Epochs: %s" % epochs)
            _logger.info("Cost: %s" % newcost)
            if (forced_epochs == 0 and
                    newcost >= oldcost - finish_threshold *
                    self.corpus_coding.boundaries):
                break
            if forced_epochs > 0:
                forced_epochs -= 1
        _logger.info("Done.")
        return epochs, newcost

    def train_online(self, data, count_modifier=None, epoch_interval=10000,
                     algorithm='recursive', algorithm_params=()):
        if count_modifier is not None:
            counts = {}

        _logger.info("Starting online training")

        epochs = 0
        i = 0
        more_tokens = True
        while more_tokens:
            self._epoch_update(epochs)
            newcost = self.get_cost()
            _logger.info("Tokens processed: %s\tCost: %s" % (i, newcost))

            for _ in _progress(range(epoch_interval)):
                try:
                    _, _, w = next(data)
                except StopIteration:
                    more_tokens = False
                    break

                if count_modifier is not None:
                    if not w in counts:
                        c = 0
                        counts[w] = 1
                        addc = 1
                    else:
                        c = counts[w]
                        counts[w] = c + 1
                        addc = count_modifier(c + 1) - count_modifier(c)
                    if addc > 0:
                        self._add_compound(w, addc)
                else:
                    self._add_compound(w, 1)
                if algorithm == 'recursive':
                    segments = self._recursive_optimize(w, *algorithm_params)
                elif algorithm == 'viterbi':
                    segments = self._viterbi_optimize(w, *algorithm_params)
                else:
                    raise Error("unknown algorithm '%s'" % algorithm)
                _logger.debug("#%s: %s -> %s" %
                              (i, w, _constructions_to_str(segments)))
                i += 1

            epochs += 1

        self._epoch_update(epochs)
        newcost = self.get_cost()
        _logger.info("Tokens processed: %s\tCost: %s" % (i, newcost))
        return epochs, newcost

    def viterbi_segment(self, compound, addcount=1.0, maxlen=30):
        """Find optimal segmentation using the Viterbi algorithm.

        Arguments:
          compound -- compound to be segmented
          addcount -- constant for additive smoothing (0 = no smoothing)
          maxlen -- maximum length for the constructions

        If additive smoothing is applied, new complex construction types can
        be selected during the search. Without smoothing, only new
        single-atom constructions can be selected.

        Returns the most probable segmentation and its log-probability.

        """
        clen = len(compound)
        grid = [(0.0, None)]
        if self.corpus_coding.tokens + addcount > 0:
            logtokens = math.log(self.corpus_coding.tokens + addcount)
        else:
            logtokens = 0
        badlikelihood = clen * logtokens + 1.0
        # Viterbi main loop
        for t in range(1, clen + 1):
            # Select the best path to current node.
            # Note that we can come from any node in history.
            bestpath = None
            bestcost = None
            for pt in range(max(0, t - maxlen), t):
                if grid[pt][0] is None:
                    continue
                cost = grid[pt][0]
                construction = compound[pt:t]
                if (construction in self.analyses and
                        len(self.analyses[construction].splitloc) == 0):
                    if self.analyses[construction].count <= 0:
                        raise Error("Construction count of '%s' is %s" %
                                    (construction,
                                     self.analyses[construction].count))
                    cost += (logtokens -
                             math.log(self.analyses[construction].count +
                                      addcount))
                elif addcount > 0:
                    if self.corpus_coding.tokens == 0:
                        cost += ((addcount * math.log(addcount)
                                 + self.lexicon_coding.get_codelength(construction))
                                 / self.corpus_coding.weight)
                    else:
                        cost += ((logtokens - math.log(addcount)
                                  + ((self.lexicon_coding.boundaries +
                                      addcount) *
                                     math.log(self.lexicon_coding.boundaries
                                              + addcount))
                                  - (self.lexicon_coding.boundaries
                                     * math.log(self.lexicon_coding.boundaries))
                                  + self.lexicon_coding.get_codelength(construction))
                                 / self.corpus_coding.weight)
                elif len(construction) == 1:
                    cost += badlikelihood
                else:
                    continue
                if bestcost is None or cost < bestcost:
                    bestcost = cost
                    bestpath = pt
            grid.append((bestcost, bestpath))
        constructions = []
        cost, path = grid[-1]
        lt = clen + 1
        while path is not None:
            t = path
            constructions.append(compound[t:lt])
            path = grid[t][1]
            lt = t
        constructions.reverse()
        return constructions, cost


class Annotations:
    """Annotated data for semi-supervised learning."""

    def __init__(self):
        """Initialize a new instance of annotated data."""
        self.types = 0
        self.analyses = {}

    def get_types(self):
        """Return the number of annotated compound types."""
        return self.types

    def get_compounds(self):
        """Return the annotated compounds."""
        return self.analyses.keys()

    def get_data(self):
        """Return the annotated compounds and their analyses."""
        return self.analyses.items()

    def has_analysis(self, compound):
        """Return whether the given compound has annotation."""
        return compound in self.analyses

    def get_analyses(self, compound):
        """Return the analyses for the given compound."""
        return self.analyses[compound]

    def load(self, data):
        """Load annotations from file.

        Arguments:
            datafile -- filename
            separator -- regexp for separating constructions in one analysis
            comment_re -- regexp for separating alternative analyses

        """

        for compound, analyses in data:
            self.analyses[compound] = analyses

        self.types = len(self.analyses)


class Encoding(object):

    def __init__(self, weight=1.0):
        self.logtokensum = 0.0
        self.tokens = 0
        self.boundaries = 0
        self.weight = weight

    def get_types(self):
        return 0

    _log2pi = math.log(2 * math.pi)

    @classmethod
    def _logfactorial(cls, n):
        """Calculate logarithm of n!.

        For large n (n > 20), use Stirling's approximation.

        """
        if n < 2:
            return 0.0
        if n < 20:
            return math.log(math.factorial(n))
        logn = math.log(n)
        return n * logn - n + 0.5 * (logn + cls._log2pi)

    def frequency_distribution_cost(self):
        """Calculate -log[(M - 1)! (N - M)! / (N - 1)!] for M types and N
        tokens.

        """
        types = self.get_types()
        tokens = self.tokens + self.boundaries
        if types < 2:
            return 0.0
        return (self._logfactorial(tokens - 1) -
                self._logfactorial(types - 1) -
                self._logfactorial(tokens - types))


    def permutations_cost(self):
        return -self._logfactorial(self.boundaries)

    def update_count(self, construction, old_count, new_count):
        self.tokens += new_count - old_count
        if old_count > 1:
            self.logtokensum -= old_count * math.log(old_count)
        if new_count > 1:
            self.logtokensum += new_count * math.log(new_count)

    def get_cost(self):
        if self.boundaries == 0:
            return 0.0

        n = self.tokens + self.boundaries
        return  ((n * math.log(n) -
                  self.boundaries * math.log(self.boundaries) -
                  self.logtokensum) * self.weight
                 + self.permutations_cost()
                 + self.frequency_distribution_cost())

class CorpusEncoding(Encoding):

    def __init__(self, lexicon_encoding, weight=1.0):
        super(CorpusEncoding, self).__init__(weight)
        self.lexicon_encoding = lexicon_encoding

    def get_types(self):
        return self.lexicon_encoding.boundaries


class AnnotatedCorpusEncoding(CorpusEncoding):

    def __init__(self, corpus, annotated_corpus_weight=None, penalty=-9999.9):
        super(AnnotatedCorpusEncoding, self).__init__(annotated_corpus_weight)

        self.do_update_weight = True
        self.weight = None

        if annotated_corpus_weight is not None:
            self.do_update_weight = False
            self.weight = annotated_corpus_weight

        self.penalty = penalty
        self.constructions = collections.Counter()
        self.corpus = corpus

    def set_constructions(self, constructions):
        self.constructions = collections.Counter(constructions)
        self.tokens = 0
        self.logtokensum = 0.0

    def update_count(self, construction, old_count, new_count):
        if construction in self.constructions:
            annot_count = self.constructions[construction]
            if old_count > 1:
                self.logtokensum -= annot_count * math.log(old_count)
            if new_count > 1:
                self.logtokensum += annot_count * math.log(new_count)

            if old_count == 0:
                self.logtokensum -= annot_count * self.penalty
            if new_count == 0:
                self.logtokensum += annot_count * self.penalty

    def update_weight(self):
        if not self.do_update_weight:
            return

        old = self.weight
        self.weight = (self.corpus.weight * float(self.corpus.boundaries) /
                       self.types)

        if self.weight != old:
            _logger.info("Corpus weight of annotated data set to %s"
                         % self.weight)


class LexiconEncoding(Encoding):

    def __init__(self):
        super(LexiconEncoding, self).__init__()
        self.atoms = collections.Counter()

    def get_types(self):
        return len(self.atoms) + 1

    def add(self, construction):
        self.boundaries += 1
        for atom in construction:
            c = self.atoms[atom]
            self.atoms[atom] = c + 1
            self.update_count(atom, c, c + 1)

    def remove(self, construction):
        self.boundaries -= 1
        for atom in construction:
            c = self.atoms[atom]
            self.atoms[atom] = c - 1
            self.update_count(atom, c, c - 1)

    def get_codelength(self, construction):
        """Return an approximate codelength for new construction."""
        l = len(construction) + 1
        cost = l * math.log(self.tokens + l)
        cost -= math.log(self.boundaries)
        for atom in construction:
            if atom in self.atoms:
                c = self.atoms[atom]
            else:
                c = 1
            cost -= math.log(c)
        return cost


def _boundary_recall(prediction, reference):
    """Calculate average boundary recall for given segmentations."""
    rec_total = 0
    rec_sum = 0.0
    for pre_list, ref_list in zip(prediction, reference):
        best = -1
        for ref in ref_list:
            # list of internal boundary positions
            ref_b = set(reduce(lambda x, y: x + [(x[-1] + len(y))],
                               ref, [0])[1:-1])
            if len(ref_b) == 0:
                best = 1.0
                break
            for pre in pre_list:
                pre_b = set(reduce(lambda x, y: x + [(x[-1] + len(y))],
                                   pre, [0])[1:-1])
                r = len(ref_b.intersection(pre_b)) / float(len(ref_b))
                if r > best:
                    best = r
        if best >= 0:
            rec_sum += best
            rec_total += 1
    return rec_sum, rec_total


def _bpr_evaluation(prediction, reference):
    """Return boundary precision, recall, and F-score for segmentations."""
    rec_s, rec_t = _boundary_recall(prediction, reference)
    pre_s, pre_t = _boundary_recall(reference, prediction)
    rec = rec_s / rec_t
    pre = pre_s / pre_t
    f = 2.0 * pre * rec / (pre + rec)
    return pre, rec, f


def _estimate_segmentation_dir(segments, annotations, threshold=0.01):
    """Estimate if the given compounds are under- or oversegmented.

    The decision is based on the difference between boundary precision
    and recall values for the given sample of segmented data.

    Arguments:
      segments -- list of predicted segmentations
      annotations -- list of reference segmentations
      threshold -- maximum threshold for the difference between
                   predictions and reference

    Return 1 in the case of oversegmentation, -1 in the case of
    undersegmentation, and 0 if no changes are required.

    """
    pre, rec, f = _bpr_evaluation(map(lambda x: [x], segments), annotations)
    _logger.info("Boundary evaluation: precision %.4f; recall %.4f" %
                 (pre, rec))
    if abs(pre - rec) < threshold:
        return 0
    elif rec > pre:
        return 1
    else:
        return -1


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog='morfessor.py',
        description="""
Morfessor %s

Copyright (c) 2012, Sami Virpioja and Peter Smit
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

1.  Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.

2.  Redistributions in binary form must reproduce the above
    copyright notice, this list of conditions and the following
    disclaimer in the documentation and/or other materials provided
    with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

Command-line arguments:
""" % __version__,
        epilog="""
Simple usage examples (training and testing):

  %(prog)s -t training_corpus.txt -s model.pickled
  %(prog)s -l model.pickled -T test_corpus.txt -o test_corpus.segmented

Interactive use (read corpus from user):

  %(prog)s -m online -v 2 -t -

""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False)

    # Options for input data files
    add_arg = parser.add_argument_group('input data files').add_argument
    add_arg('-l', '--load', dest="loadfile", default=None, metavar='<file>',
            help="load existing model from file (pickled model object)")
    add_arg('-L', '--load-segmentation', dest="loadsegfile", default=None,
            metavar='<file>',
            help="load existing model from segmentation "
                 "file (Morfessor 1.0 format)")
    add_arg('-t', '--traindata', dest='trainfiles', action='append',
            default=[], metavar='<file>',
            help="input corpus file(s) for training (text or gzipped text; "
                 "use '-' for standard input; add several times in order to "
                 "append multiple files)")
    add_arg('-T', '--testdata', dest='testfiles', action='append',
            default=[], metavar='<file>',
            help="input corpus file(s) to analyze (text or gzipped text;  "
                 "use '-' for standard input; add several times in order to "
                 "append multiple files)")
    add_arg('-o', '--output', dest="outfile", default='-', metavar='<file>',
            help="output file for test data results (for standard output, "
                 "use '-'; default '%(default)s')")

    # Options for output data files
    add_arg = parser.add_argument_group('output data files').add_argument
    add_arg('-s', '--save', dest="savefile", default=None, metavar='<file>',
            help="save final model to file (pickled model object)")
    add_arg('-S', '--save-segmentation', dest="savesegfile", default=None,
            metavar='<file>',
            help="save model segmentations to file (Morfessor 1.0 format)")
    add_arg('-x', '--lexicon', dest="lexfile", default=None, metavar='<file>',
            help="output final lexicon to given file")

    # Options for data formats
    add_arg = parser.add_argument_group(
        'data format options').add_argument
    add_arg('-e', '--encoding', dest='encoding', metavar='<encoding>',
            help="encoding of input and output files (if none is given, "
            "both the local encoding and UTF-8 are tried)")
    add_arg('--traindata-list', dest="list", default=False,
            action='store_true',
            help="input file(s) for batch training are lists "
                 "(one compound per line, optionally count as a prefix)")
    add_arg('--atom-separator', dest="separator", type=str, default=None,
            metavar='<regexp>',
            help="atom separator regexp (default %(default)s)")
    add_arg('--compound-separator', dest="cseparator", type=str, default='\W+',
            metavar='<regexp>',
            help="compound separator regexp (default '%(default)s')")

    # Options for model training
    add_arg = parser.add_argument_group(
        'training and segmentation options').add_argument
    add_arg('-m', '--mode', dest="trainmode", default='init+batch',
            metavar='<mode>',
            choices=['none', 'batch', 'init', 'init+batch', 'online',
                     'online+batch'],
            help="training mode ('none', 'init', 'batch', 'init+batch', "
            "'online', or 'online+batch'; default '%(default)s')")
    add_arg('-a', '--algorithm', dest="algorithm", default='recursive',
            metavar='<algorithm>', choices=['recursive', 'viterbi'],
            help="algorithm type ('recursive', 'viterbi'; default "
                 "'%(default)s')")
    add_arg('-d', '--dampening', dest="dampening", type=str, default='none',
            metavar='<type>', choices=['none', 'log', 'ones'],
            help="frequency dampening for training data ('none', 'log', or "
                 "'ones'; default '%(default)s')")
    add_arg('-f', '--forcesplit', dest="forcesplit", type=list, default=['-'],
            metavar='<list>',
            help="force split on given atoms (default %(default)s)")
    add_arg('-r', '--randseed', dest="randseed", default=None,
            metavar='<seed>',
            help="seed for random number generator")
    add_arg('-R', '--randsplit', dest="splitprob", default=None, type=float,
            metavar='<float>',
            help="initialize model by random splitting using the given split "
                 "probability (default no splitting)")
    add_arg('--skips', dest="skips", default=False, action='store_true',
            help="use random skips for frequently seen compounds to speed up "
                 "training")
    add_arg('--batch-minfreq', dest="freqthreshold", type=int, default=1,
            metavar='<int>',
            help="compound frequency threshold for batch training (default "
                 "%(default)s)")
    add_arg('--online-epochint', dest="epochinterval", type=int,
            default=10000, metavar='<int>',
            help="epoch interval for online training (default %(default)s)")
    add_arg('--viterbi-smoothing', dest="viterbismooth", default=0,
            type=float, metavar='<float>',
            help="additive smoothing parameter for Viterbi training "
            "and segmentation (default %(default)s)")
    add_arg('--viterbi-maxlen', dest="viterbimaxlen", default=30,
            type=int, metavar='<int>',
            help="maximum construction length in Viterbi training "
            "and segmentation (default %(default)s)")

    # Options for semi-supervised model training
    add_arg = parser.add_argument_group(
        'semi-supervised training options').add_argument
    add_arg('-A', '--annotations', dest="annofile", default=None,
            metavar='<file>',
            help="load annotated data for semi-supervised learning")
    add_arg('-D', '--develset', dest="develfile", default=None,
            metavar='<file>',
            help="load annotated data for tuning the corpus weight parameter")
    add_arg('-w', '--corpusweight', dest="corpusweight", type=float,
            default=1.0, metavar='<float>',
            help="corpus weight parameter (default %(default)s); "
            "sets the initial value if --develset is used")
    add_arg('-W', '--annotationweight', dest="annotationweight",
            type=float, default=None, metavar='<float>',
            help="corpus weight parameter for annotated data (if unset, the "
                 "weight is set to balance the number of tokens in annotated "
                 "and unannotated data sets)")

    # Options for logging
    add_arg = parser.add_argument_group('logging options').add_argument
    add_arg('-v', '--verbose', dest="verbose", type=int, default=1,
            metavar='<int>',
            help="verbose level; controls what is written to the standard "
                 "error stream or log file (default %(default)s)")
    add_arg('--logfile', dest='log_file', metavar='<file>',
            help="write log messages to file in addition to standard "
            "error stream")

    add_arg = parser.add_argument_group('other options').add_argument
    add_arg('-h', '--help', action='help',
            help="show this help message and exit")
    add_arg('--version', action='version',
            version='%(prog)s ' + __version__,
            help="show version number and exit")

    args = parser.parse_args(argv)

    if args.verbose >= 2:
        loglevel = logging.DEBUG
    elif args.verbose >= 1:
        loglevel = logging.INFO
    else:
        loglevel = logging.WARNING

    logging_format = '%(asctime)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    default_formatter = logging.Formatter(logging_format, date_format)
    plain_formatter = logging.Formatter('%(message)s')
    logging.basicConfig(level=loglevel)
    _logger.propagate = False  # do not forward messages to the root logger

    # Basic settings for logging to the error stream
    ch = logging.StreamHandler()
    ch.setLevel(loglevel)
    ch.setFormatter(default_formatter)
    _logger.addHandler(ch)

    # Settings for when log_file is present
    if args.log_file is not None:
        fh = logging.FileHandler(args.log_file, 'w')
        fh.setLevel(loglevel)
        fh.setFormatter(default_formatter)
        _logger.addHandler(fh)
        # If logging to a file, make INFO the highest level for the
        # error stream
        ch.setLevel(max(loglevel, logging.INFO))
        # Also, don't print timestamps to the error stream
        ch.setFormatter(plain_formatter)

    # If debug messages are printed to screen or if stderr is not a tty (but
    # a pipe or a file), don't show the progressbar
    global show_progress_bar
    if (ch.level > logging.INFO or
            (hasattr(sys.stderr, 'isatty') and not sys.stderr.isatty())):
        show_progress_bar = False

    if (args.loadfile is None and
            args.loadsegfile is None and
            len(args.trainfiles) == 0):
        parser.error("either model file or training data should be defined")

    if args.randseed is not None:
        random.seed(args.randseed)

    io = MorfessorIO(encoding=args.encoding,
                     compound_separator=args.cseparator,
                     atom_separator=args.separator)

    # Load exisiting model or create a new one
    if args.loadfile is not None:
        model = io.read_binary_model_file(args.loadfile)

    else:
        model = BaselineModel(forcesplit_list=args.forcesplit,
                              corpusweight=args.corpusweight,
                              use_skips=args.skips)

    if args.loadsegfile is not None:
        model.load_segmentations(io.read_segmentation_file(args.loadsegfile))

    if args.annofile is not None:
        annotations = Annotations()
        annotations.load(io.read_annotations_file(args.annofile))
        model.set_annotations(annotations, args.annotationweight)

    if args.develfile is not None:
        develannots = Annotations()
        develannots.load(io.read_annotations_file(args.develfile))
    else:
        develannots = None

    # Set frequency dampening function
    if args.dampening == 'none':
        dampfunc = lambda x: x
    elif args.dampening == 'log':
        dampfunc = lambda x: int(round(math.log(x + 1, 2)))
    elif args.dampening == 'ones':
        dampfunc = lambda x: 1
    else:
        parser.error("unknown dampening type '%s'" % args.dampening)

    # Set algorithm parameters
    if args.algorithm == 'viterbi':
        algparams = (args.viterbismooth, args.viterbimaxlen)
    else:
        algparams = ()

    # Train model
    if args.trainmode == 'none':
        pass
    elif args.trainmode == 'batch':
        if len(model._get_compounds()) == 0:
            _logger.warning("Model contains no compounds for batch training."
                            " Use 'init+batch' mode to add new data.")
        else:
            if len(args.trainfiles) > 0:
                _logger.warning("Training mode 'batch' ignores new data "
                                "files. Use 'init+batch' or 'online' to "
                                "add new compounds.")
            ts = time.time()
            e, c = model.train_batch(args.algorithm, algparams, develannots)
            te = time.time()
            _logger.info("Epochs: %s" % e)
            _logger.info("Final cost: %s" % c)
            _logger.info("Training time: %.3fs" % (te - ts))
    elif len(args.trainfiles) > 0:
        ts = time.time()
        if args.trainmode == 'init':
            for f in args.trainfiles:
                if args.list:
                    data = io.read_corpus_list_file(f)
                else:
                    data = io.read_corpus_file(f)
            c = model.load_data(data, args.freqthreshold, dampfunc,
                                args.splitprob)
        elif args.trainmode == 'init+batch':
            for f in args.trainfiles:
                if args.list:
                    data = io.read_corpus_list_file(f)
                else:
                    data = io.read_corpus_file(f)
            model.load_data(data, args.freqthreshold, dampfunc, args.splitprob)
            e, c = model.train_batch(args.algorithm, algparams, develannots)
            _logger.info("Epochs: %s" % e)
        elif args.trainmode == 'online':
            data = io.read_corpus_files(args.trainfiles)
            e, c = model.train_online(data, dampfunc, args.epochinterval,
                                      args.algorithm, algparams)
            _logger.info("Epochs: %s" % e)
        elif args.trainmode == 'online+batch':
            data = io.read_corpus_files(args.trainfiles)
            e, c = model.train_online(data, dampfunc, args.epochinterval,
                                      args.algorithm, algparams)
            e, c = model.train_batch(args.algorithm, algparams, develannots)
            _logger.info("Epochs: %s" % e)
        else:
            parser.error("unknown training mode '%s'" % args.trainmode)
        te = time.time()
        _logger.info("Final cost: %s" % c)
        _logger.info("Training time: %.3fs" % (te - ts))
    else:
        _logger.warning("No training data files specified.")

    # Save model
    if args.savefile is not None:
        io.write_binary_model_file(args.savefile, model)

    if args.savesegfile is not None:
        io.write_segmentation_file(args.savesegfile, model.get_segmentations())

    # Output lexicon
    if args.lexfile is not None:
        io.write_lexicon_file(args.lexfile, model.get_constructions())

    # Segment test data
    if len(args.testfiles) > 0:
        _logger.info("Segmenting test data...")
        with io._open_text_file_write(args.outfile) as fobj:
            testdata = io.read_corpus_files(args.testfiles)
            i = 0
            for _, _, compound in testdata:
                constructions, logp = model.viterbi_segment(
                    compound, args.viterbismooth, args.viterbimaxlen)
                fobj.write("%s\n" % ' '.join(constructions))
                i += 1
                if i % 10000 == 0:
                    sys.stderr.write(".")
            sys.stderr.write("\n")
        _logger.info("Done.")

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as e:
        _logger.error("Fatal Error %s %s" % (type(e), str(e)))
        raise
