import os
import re
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd

import vcf
from BCBio import GFF
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.Alphabet import IUPAC


def convert_vcf(fname):
    """Convert VCF to JSON."""
    output = []

    if os.path.getsize(fname) == 0:
        print(f'Empty VCF: "{fname}"')
        return output

    print(f'Parsing VCF: "{fname}"')
    with open(fname) as fd:
        vcf_reader = vcf.Reader(fd)

        for record in vcf_reader:
            output.append(
                {
                    "position": record.POS,
                    "reference": record.REF,
                    "variant": [v.sequence for v in record.ALT],
                    "frequency": round(np.mean(
                        [v for k, v in record.INFO.items() if k.startswith("Freq")]
                    ), 3),
                    "posterior": round(1 - 10**(-record.QUAL / 10), 3)
                }
            )

    return json.dumps(output)


def parse_gff(fname):
    """Convert GFF to map."""
    features = []

    print(f'Parsing GFF: "{fname}"')
    with open(fname) as fd:
        for record in GFF.parse(fd):
            for feature in record.features:
                features.append(
                    {
                        "id": record.id,
                        "type": feature.type,
                        "name": feature.qualifiers.get('Name', [feature.id])[0],
                        "start": int(feature.location.start),
                        "end": int(feature.location.end),
                    }
                )
    return features


def arrange_gff_data(features):
    """Add row number to each feature."""
    features.sort(key=lambda f: f["start"])

    rows = []
    for feature in features:
        if not rows:
            feature["row_cnt"] = 0
            rows.append([feature])
        else:
            found = False
            for idx, row in enumerate(rows):
                if row[-1]["end"] <= feature["start"]:
                    feature["row_cnt"] = idx
                    row.append(feature)
                    found = True
                    break
            if not found:
                feature["row_cnt"] = len(rows)
                rows.append([feature])

    return [item for row in rows for item in row]


def get_gff_data(gff_dir):
    """Returns a map with filename key and gff json data."""
    # Hardcode metainformation for the GFF file provided in the repository.
    gff_metainfo = {}
    gff_metainfo["Genes_NC_045512.2"] = "Genes (ORFs)"
    gff_metainfo[
        "Sars-Cov2_Mature_products"
    ] = "Mature processed protein products"
    gff_metainfo["Sars-Cov2_Uniprot_domains"] = "Uniprot domains"
    gff_metainfo[
        "Sars-Cov2_TM_domains"
    ] = "Uniprot predicted transmembrane regions"

    gff_map = {}
    if not gff_dir:
        print("No gff directory provided, skipping.")
        return gff_map
    for path in os.listdir(gff_dir):
        full_path = os.path.join(gff_dir, path)
        description = os.path.splitext(path)[0]
        if description in gff_metainfo:
            description = gff_metainfo[description]
        gff_map[description] = arrange_gff_data(parse_gff(full_path))
    return gff_map


def get_primers_data(full_path, consensus):
    """Returns a map with filename key and primers json data."""
    # Hardcode metainformation for the filenames provided in the repository.
    primers_metainfo = {}
    primers_metainfo["nCoV-2019"] = "ARTIC bioinformatics primers for nanopore sequencing of nCoV2019"

    primers_map = {}
    if not full_path:
        print("No primers table provided, skipping.")
        return primers_map
    else:
        print(f'Parsing GFF: "{full_path}"')
    consensus_upper = consensus.upper()
    description = os.path.splitext(os.path.basename(full_path))[0]
    if description in primers_metainfo:
        description = primers_metainfo[description]
    csv = pd.read_csv(full_path, sep=',')
    primers = [{'name': row[0], "seq":row[1].upper()}
               for row in csv[['name', 'seq']].values]
    primer_locations = []
    for entry in primers:
        lookup_sequence = entry['seq']
        # If the sequence corresponds to a `right` primer, then reverse and
        # complement.
        if "RIGHT" in entry['name'].upper():
            seq = Seq(lookup_sequence, IUPAC.unambiguous_dna)
            lookup_sequence = str(seq.reverse_complement())
        offsets = [
            m.start() for m in re.finditer(
                '(?=' + lookup_sequence + ')',
                consensus_upper)]
        for offset in offsets:
            primer_locations.append({'name': entry['name'],
                                     'seq': entry['seq'],
                                     'start': offset,
                                     'end': offset + len(entry['seq']) - 1})
    if primer_locations:
        primers_map[description] = arrange_gff_data(primer_locations)
    else:
        print(f'No primer was mapped from "{full_path}".')
    return primers_map


def convert_coverage(fname, sample_name, genome_length):
    """Convert the read coverage to bp coverage."""
    print(f'Parsing coverage: "{fname}"')
    csv = pd.read_csv(fname, sep="\t", index_col=0, header=0)
    return csv[sample_name].values.tolist()


def assemble_visualization_webpage(
    consensus_file,
    coverage_file,
    vcf_file,
    gff_directory,
    primers_file,
    html_file_in,
    html_file_out,
    wildcards_dataset,
    reference_file,
):
    # parse the sample name
    path_components = os.path.normpath(wildcards_dataset).split(os.path.sep)
    sample_name = '/'.join(path_components[-2:])

    # parse the consensus sequence
    print(f'Parsing consensus: "{consensus_file}"')
    consensus = next(SeqIO.parse(consensus_file, "fasta")).seq.upper()

    # parse coverage file
    coverage = convert_coverage(
        coverage_file, sample_name.replace(
            '/', '-'), len(consensus))

    # load biodata in json format
    vcf_json = convert_vcf(vcf_file)
    gff_map = get_gff_data(gff_directory)
    primers_map = get_primers_data(primers_file, str(consensus))

    # parse the reference name
    reference_name = re.search(
        r"(.+)\.fa.*",
        os.path.basename(reference_file)
    ).group(1).replace('_', '&nbsp;')

    embed_code = f"""
        var sample_name = \"{sample_name}\"
        var consensus = \"{consensus}\"
        var coverage = {coverage}
        var vcfData = {vcf_json}
        var gffData = {gff_map}
        var primerData = {primers_map}
        var reference_name = \"{reference_name}\"
    """

    # assemble webpage
    with open(html_file_in) as fd:
        raw_html = fd.read()

    # TODO: make this more robust
    mod_html = raw_html.replace("{EXTERNAL_SNAKEMAKE_CODE_MARKER}", embed_code)

    with open(html_file_out, "w") as fd:
        fd.write(mod_html)


def main():
    """Parse command line, run default functions."""
    import argparse
    # parse command line
    # create the top-level parser
    parser = argparse.ArgumentParser(description="Generate HTML visual report from VCF variants")
    parser.add_argument('-f','--consensus', metavar='FASTA', required=False,
        type=str, dest='consensus_file', help="consensus sequence of this sample")
    parser.add_argument('-c','--coverage', metavar='TSV', required=False,
        default='variants/coverage.tsv',
        type=str, dest='coverage_file', help="global coverage table")
    parser.add_argument('-v','--vcf', metavar='VCF', required=False,
        type=str, dest='vcf_file', help="VCF containing the SNPs to be visualised")
    parser.add_argument('-g','--gff', metavar='DIR', required=False,
        type=str, dest='gff_directory', help="directory containing GFF annotations")
    parser.add_argument('-p','--primers', metavar='CSV', required=False,
        type=str, dest='primers_file', help="table with primers")
    parser.add_argument('-t','--template', metavar='HTML', required=False,
        default=f'{os.path.dirname(__file__)}/visualization.html',
        type=str, dest='html_file_in', help="HTML template used to generate visual report")
    parser.add_argument('-o','--output', metavar='HTML', required=False,
        type=str, dest='html_file_out', help="produced HTML report")
    parser.add_argument('-w','--wildcards', metavar='SAMPLE/DATE', required=False,
        type=str, dest='wildcards_dataset', help="sample's two-level directory hierarchy prefix")
    parser.add_argument('-r','--reference', metavar='FASTA', required=False,
        default='variants/cohort_consensus.fasta',
        type=str, dest='reference_file', help="reference against which SNVs were called (e.g.: cohort's consensus)")

    args = parser.parse_args()

    # defaults which can be guess from one another
    if args.vcf_file  == None:  # e.g.: samples/140074_395_D02/20200615_J6NRK/variants/SNVs/snvs.vcf
        assert args.wildcards_dataset != None, 'cannot automatically find VCF without wildcards'
        args.vcf_file = os.path.join(args.wildcards_dataset, 'variants', 'SNVs', 'snvs.vcf')

    if args.consensus_file  == None:  # e.g.: samples/140074_395_D02/20200615_J6NRK/references/ref_majority.fasta
        assert args.wildcards_dataset != None, 'cannot automatically find consensus without wildcards'
        args.consensus_file = os.path.join(args.wildcards_dataset, 'references', 'ref_majority.fasta')

    if args.wildcards_dataset == None:
        assert args.vcf_file != None and args.consensus != None, 'cannot deduce wilcards without a consensus and a vcf'
        try1 = '/'.join(os.path.normpath(args.vcf_file).split(os.path.sep)[-5:-3])
        try2 = '/'.join(os.path.normpath(args.consensus_file).split(os.path.sep)[-4:-2])
        assert try1 == try2, f'cannot deduce wildcards automatically from <{args.vcf_file}> and <{args.consensus_file}>, please specify explicitly using `--wirdcards`'
        args.wildcards_dataset = try1

    if args.html_file_out == None:
        args.html_file_out = os.path.join(args.wildcards_dataset, 'visualization', 'index.html')

    # run the visual report generator
    assemble_visualization_webpage(**vars(args))


if __name__ == "__main__":
    main()