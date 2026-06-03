#!/usr/bin/env nextflow

params {
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/solutions/samplesheet-wrong-paths.csv"
}

/**
 * Quality control fastq
 */
    
process fastqc {
    container 'quay.io/biocontainers/fastqc:0.11.9--0'

    input:
    tuple val(sample), path(read)  
    
    script:
    """
    fastqc ${read}
    """
}

workflow {
    def reads_ch = channel.fromPath( params.samplesheet )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)] ) }
        .view()

    fastqc(reads_ch)
}