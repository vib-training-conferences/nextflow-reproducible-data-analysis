#!/usr/bin/env nextflow

process FASTP {
    // DIRECTIVES: set the docker container, the directory to output to, and a tag to follow along which sample is currently being processed
    container 'quay.io/biocontainers/fastp:1.0.1--heae3180_0'
    publishDir {"${params.outdir}/fastp"}, mode: 'copy', overwrite: true
    tag "${sample}"

    input:
    tuple val(sample), path(cookies)
    val fw_primer
    val rv_primer

    output:
    tuple val(sample), path('*_trimmed.fastq')

    script:
    """
    fastp --in1 ${cookies[0]} \
        --in2 ${cookies[1]} \
        --out1 ${sample}_R1_trimmed.fastq \
        --out2 ${sample}_R2_trimmed.fastq \
        --qualified_quality_phred 28 \
        --cut_tail \
        --length_required 30 \
        --n_base_limit 0 \
        --adapter_sequence ${fw_primer} \
        --adapter_sequence_r2 ${rv_primer}
    """
}