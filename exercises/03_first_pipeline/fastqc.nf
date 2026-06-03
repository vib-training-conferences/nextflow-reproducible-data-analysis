#!/usr/bin/env nextflow

params{
    reads: String = "${launchDir}/data/*.fq.gz"
}

/**
 * Quality control fastq
 */
    
process fastqc {
    container 'quay.io/biocontainers/fastqc:0.11.9--0'

    input:
    path read
    
    script:
    """
    fastqc ${read}
    """
}

workflow {
    def reads_ch = channel
        .fromPath( params.reads )
        .view()
    fastqc(reads_ch)
}