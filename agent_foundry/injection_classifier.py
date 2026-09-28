"""Trainable prompt-injection detector with no third-party dependencies.

The default marker list in `guardrails.looks_like_injection` has high
precision but misses paraphrased and non-English attacks. This module offers
a drop-in alternative: multinomial naive Bayes over word unigrams/bigrams and
character 3-5-grams (log-count features, Laplace smoothing). It is small
enough to train in-process from a labeled corpus and to ship as JSON.

It is a statistical filter, not a security boundary: it lowers the rate of
injected instructions reaching the model, and should sit in front of the
architectural controls (least-authority tools, deny-first admission), never
replace them.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

_WORD = re.compile(r"\w+", re.UNICODE)


def features(text: str) -> Counter:
    lowered = text.lower()
    words = _WORD.findall(lowered)
    feats: Counter = Counter(f"w:{w}" for w in words)
    feats.update(f"b:{a}_{b}" for a, b in zip(words, words[1:]))
    squashed = " ".join(words)
    for n in (3, 4, 5):
        feats.update(f"c{n}:{squashed[i:i + n]}" for i in range(max(0, len(squashed) - n + 1)))
    return feats


@dataclass
class InjectionClassifier:
    alpha: float = 0.5
    threshold: float = 0.5
    log_prior: dict[int, float] = field(default_factory=dict)
    log_likelihood: dict[int, dict[str, float]] = field(default_factory=dict)
    log_unseen: dict[int, float] = field(default_factory=dict)

    def fit(self, texts: Iterable[str], labels: Iterable[int]) -> "InjectionClassifier":
        counts: dict[int, Counter[str]] = {0: Counter(), 1: Counter()}
        docs: Counter[int] = Counter()
        for text, label in zip(texts, labels):
            docs[int(label)] += 1
            # log(1 + tf) damps repeated n-grams in long inputs.
            counts[int(label)].update({k: math.log1p(v) for k, v in features(text).items()})
        vocab = set(counts[0]) | set(counts[1])
        total = sum(docs.values())
        for c in (0, 1):
            mass = sum(counts[c].values()) + self.alpha * len(vocab)
            self.log_prior[c] = math.log((docs[c] + 1) / (total + 2))
            self.log_likelihood[c] = {f: math.log((counts[c][f] + self.alpha) / mass) for f in counts[c]}
            self.log_unseen[c] = math.log(self.alpha / mass)
        return self

    def probability(self, text: str) -> float:
        scores = {}
        for c in (0, 1):
            table, unseen = self.log_likelihood[c], self.log_unseen[c]
            scores[c] = self.log_prior[c] + sum(math.log1p(v) * table.get(f, unseen) for f, v in features(text).items())
        m = max(scores.values())
        return math.exp(scores[1] - m) / (math.exp(scores[0] - m) + math.exp(scores[1] - m))

    def __call__(self, text: str) -> bool:
        return self.probability(text) >= self.threshold

    def to_json(self) -> str:
        return json.dumps({"alpha": self.alpha, "threshold": self.threshold, "log_prior": self.log_prior,
                           "log_likelihood": self.log_likelihood, "log_unseen": self.log_unseen})

    @classmethod
    def from_json(cls, data: str) -> "InjectionClassifier":
        raw = json.loads(data)
        return cls(alpha=raw["alpha"], threshold=raw["threshold"],
                   log_prior={int(k): v for k, v in raw["log_prior"].items()},
                   log_likelihood={int(k): v for k, v in raw["log_likelihood"].items()},
                   log_unseen={int(k): v for k, v in raw["log_unseen"].items()})


def combined_detector(classifier: InjectionClassifier):
    """Flag text if either the marker list or the classifier flags it."""
    from .guardrails import looks_like_injection

    return lambda text: looks_like_injection(text) or classifier(text)
