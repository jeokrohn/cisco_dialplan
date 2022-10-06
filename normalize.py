#!/usr/bin/env python
"""
Read a bunch of patterns from a CSV file (GDPR export) and normalize them to a format that can be consumed by
WxC dial plans
"""
import logging
import os.path
import re
import sys
from collections import defaultdict
from csv import DictReader
from itertools import groupby
from typing import Iterable, Generator

CSV_PATH = 'ILS_Learned_Patterns_ForScript.csv'


def normalize(*, patterns: Iterable[str]) -> Generator[str, None, None]:
    # regex to catch patterns with [..] in it
    catch_re = re.compile(r'(?P<pre>.*)(?P<re_part>\[.+])(?P<post>.*)')
    for pattern in patterns:
        if any(c in pattern for c in '.*!'):
            print(f'illegal pattern format: {pattern}', file=sys.stderr)
            continue
        if m := catch_re.match(pattern):
            # get pre, regex, post
            pre = m.group('pre')
            re_part = m.group('re_part')
            post = m.group('post')
            # determine digits matched by the reqex in the pattern and yield normalized patterns
            digit_matcher = re.compile(re_part)
            matching_digits = (d for d in '0123456789'
                               if digit_matcher.match(d))
            logging.debug(f'expanding "{pattern}"')
            for d in matching_digits:
                expanded = f'{pre}{d}{post}'
                logging.debug(f' {expanded}')
                yield expanded
        else:
            # nothing to do, just yield the pattern
            yield pattern


def read_and_normalize(csv_name: str):
    with open(csv_name, mode='r', encoding='utf-8-sig') as csv_file:
        reader = DictReader(csv_file, dialect='excel')
        records = list(reader)
    # group patterns by remote catalog
    records.sort(key=lambda r: r['remotecatalogkey_id'])
    grouped = {catalog: set(r['pattern'] for r in riter)
               for catalog, riter in groupby(records, key=lambda r: r['remotecatalogkey_id'])}
    grouped: dict[str, set[str]]

    # some patterns might be conflicting
    normalized_by_source = defaultdict(list)
    for catalog in grouped:
        for pattern in grouped[catalog]:
            for normalized in normalize(patterns=[pattern]):
                normalized_by_source[normalized].append((catalog, pattern))
    # duplicates are normalized patterns resulting from normalization of more than one pattern
    duplicates = {n: l for n, l in normalized_by_source.items()
                  if len(l) > 1}

    # now fix the conflicts
    for duplicate in duplicates:
        # list of patterns this duplicate was normalized from
        origin_patterns = [o for _, o in duplicates[duplicate]]

        # normalization results per origin
        normalized_from_origin = {o: set(normalize(patterns=[o])) for o in origin_patterns}
        normalized_from_origin: dict[str, set[str]]

        # sort origin_patterns so that we have the most specific 1st
        origin_patterns.sort(key=lambda o: len(normalized_from_origin[o]))

        # now remove more specific patterns from least specific ones
        # need to iterate by index as we are updating members
        for i in range(len(origin_patterns) - 1):
            more_specific = normalized_from_origin[origin_patterns[i]]
            # remove these from all less specific origins
            for l in range(i + 1, len(origin_patterns)):
                less_specific = normalized_from_origin[origin_patterns[l]]
                less_specific.difference_update(more_specific)

        # now remove the original patterns from catalog and insert new ones
        print(f'Conflict resolution: {", ".join(origin_patterns)}', file=sys.stderr)
        for catalog, pattern in duplicates[duplicate]:
            print(f' Replacing pattern {pattern} in catalog "{catalog}" with '
                  f'{", ".join(sorted(normalized_from_origin[pattern]))}', file=sys.stderr)
            # remove original pattern from catalog
            grouped[catalog].difference_update([pattern])
            # .. and replace with new patterns
            grouped[catalog].update(normalized_from_origin[pattern])

    results = {}
    # normalize patterns for each remote catalog
    for catalog, patterns in grouped.items():
        normalized_patterns = list(normalize(patterns=patterns))
        normalized_patterns.sort()
        # print normalized patterns
        print('\n'.join(f'{catalog},{pattern}' for pattern in normalized_patterns))
        results[catalog] = (len(patterns), len(normalized_patterns))
    # print a summary
    before_total, after_total = 0, 0
    for catalog, (before, after) in results.items():
        before_total += before
        after_total += after
        print(f'{catalog}: {before} patterns normalized to {after} patterns', file=sys.stderr)
    print(f'{before_total} patterns normalized to {after_total} patterns', file=sys.stderr)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print(f'usage: {os.path.basename(sys.argv[0])} csvfile')
        exit(1)
    read_and_normalize(csv_name=sys.argv[1])
