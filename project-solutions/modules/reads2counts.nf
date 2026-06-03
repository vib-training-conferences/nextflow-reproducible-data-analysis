#!/usr/bin/env nextflow

process DADA2 {
    // DIRECTIVES: set the docker container, the directory to output to, and the amount of cpus to allocate to this process
    container 'blekhmanlab/dada2:1.26.0'
    publishDir {"${params.outdir}/dada2"}, mode: 'copy', overwrite: true
    cpus 4

    input:
    path(reads)

    output:
    path('counts_matrix.csv')
    path('dendrogram.png')

    script:
    """
    # We don't have to provide the full path to this script as it is in the root `bin` dir of the project
    # See: https://www.nextflow.io/docs/latest/faq.html#how-do-i-invoke-custom-scripts-and-tools

    reads2counts.r ${reads}
    """
}
