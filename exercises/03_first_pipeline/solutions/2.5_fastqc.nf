#!/usr/bin/env nextflow

params {
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/samplesheet.csv"
    outdir: String = "${launchDir}/results"
}

/**
 * Quality control fastq
 */
    
process fastqc {
    publishDir {"${params.outdir}/quality-control-${sample}/"}, mode: 'copy', overwrite: true
    container 'quay.io/biocontainers/fastqc:0.11.9--0'

    input:
    tuple val(sample), path(read)  
    
    output:
    path("*_fastqc.{zip,html}") 
    
    script:
    """
    fastqc ${read}
    """
}

workflow {
    def reads_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1), file(row.fastq_2)] ) }
        .view()

    fastqc(reads_ch)
}