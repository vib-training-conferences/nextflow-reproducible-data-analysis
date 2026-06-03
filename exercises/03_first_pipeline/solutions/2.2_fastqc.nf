#!/usr/bin/env nextflow

params {
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/samplesheet.csv"
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
        .map{ row -> tuple( row.sample, [file(row.fastq_1), file(row.fastq_2)] ) }
        .view()

    fastqc(reads_ch)
}