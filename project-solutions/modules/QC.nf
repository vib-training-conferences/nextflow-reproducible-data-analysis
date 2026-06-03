#!/usr/bin/env nextflow

process FASTQC { 
    // DIRECTIVES: set the docker container, the directory to output to, and a tag to follow along which sample is currently being processed
    container 'biocontainers/fastqc:v0.11.9_cv8'
    publishDir {"${params.outdir}/fastqc/${step}"}, mode: 'copy', overwrite: true
    tag "${sample}"

    input:
    val(step)
    tuple val(sample), path(reads)

    output:
    path('*_fastqc.{zip,html}')

    script:
    """
    fastqc -f fastq -q ${reads}
    """
}


process MULTIQC {
    // DIRECTIVES: set the docker container, the directory to output to, and a tag
    container 'ewels/multiqc'
    publishDir {"${params.outdir}/multiqc/${step}/"}, mode: 'copy', overwrite: true
    tag "${step}"

    input:
    val(step)
    path(inputfiles)

    output:
    path('*_multiqc_report.html')

    script:
    """
    multiqc --filename ${step}_multiqc_report .
    """
}


// combine the two processes into a subworkflow
workflow check_QC {
    take:
        step
        reads_ch

    main:
        FASTQC(step, reads_ch)
        
        def input_multiqc = FASTQC.out.collect()
        MULTIQC(step, input_multiqc)
}