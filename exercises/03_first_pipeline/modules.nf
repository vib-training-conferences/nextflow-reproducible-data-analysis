#!/usr/bin/env nextflow

params {
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/samplesheet.csv"
    outdir: String = "${launchDir}/results"
    slidingwindow: String = "SLIDINGWINDOW:4:15"
    avgqual: String = "AVGQUAL:30"
}

include { fastqc as fastqc_raw; fastqc as fastqc_trim } from "../../modules/fastqc" 
include { trimmomatic } from "../../modules/trimmomatic"

// Running a workflow with the defined processes here.  
workflow {
    log.info """\
        LIST OF PARAMETERS
    ================================
                GENERAL
    Samplesheet      : ${params.samplesheet}
    Output-folder    : ${params.outdir}/

            TRIMMOMATIC
    Sliding window   : ${params.slidingwindow}
    Avg quality      : ${params.avgqual}
    """

    // Channels are being created. 
    def read_pairs_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)] ) }

    read_pairs_ch.view()
    fastqc_raw(read_pairs_ch) 
    trimmomatic(read_pairs_ch, params.slidingwindow, params.avgqual)
    fastqc_trim(trimmomatic.out.trim_fq)
}
