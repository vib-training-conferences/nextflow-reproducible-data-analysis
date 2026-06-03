#!/usr/bin/env nextflow


// The input data is defined in the beginning.
params {
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/samplesheet.csv"
    outdir: String = "${launchDir}/results"
    slidingwindow: String = "SLIDINGWINDOW:4:15"
    avgqual: String = "AVGQUAL:30"
}

// Definition of a process, notice the absence of the 'from channel'.
// A process being defined, does not mean it's invoked (see workflow)
process fastqc {
    publishDir {"${params.outdir}/quality-control-${sample}/"}, mode: 'copy', overwrite: true
    container 'quay.io/biocontainers/fastqc:0.11.9--0'
    
    input:
    tuple val(sample), path(reads)

    script:
    """
    fastqc ${reads}
    """
}

// Process trimmomatic
process trimmomatic {
    publishDir {"${params.outdir}/trimmed-reads-${sample}"}, mode: 'copy'
    container 'quay.io/biocontainers/trimmomatic:0.35--6'

    // Same input as fastqc on raw reads, comes from the same channel. 
    input:
    tuple val(sample), path(reads)
    val slidingwindow
    val avgqual

    output:
    tuple val("${sample}"), path("${sample}*_P.fq"), emit: paired_fq
    tuple val("${sample}"), path("${sample}*_U.fq"), emit: unpaired_fq

    script:
    """
    trimmomatic PE -threads ${task.cpus} ${reads[0]} ${reads[1]} ${sample}1_P.fq ${sample}1_U.fq ${sample}2_P.fq ${sample}2_U.fq ${slidingwindow} ${avgqual} 
    """
}

// Running a workflow with the defined processes here.  
workflow {
    log.info """\
        LIST OF PARAMETERS
    ================================
                GENERAL
    Samplesheet      : ${params.samplesheet}
    Output-folder    : ${params.outdir}

            TRIMMOMATIC
    Sliding window   : ${params.slidingwindow}
    Avg quality      : ${params.avgqual}
    """
    // Channels are being created. 
    def read_pairs_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)] ) }

	fastqc(read_pairs_ch) 
    trimmomatic(read_pairs_ch, params.slidingwindow, params.avgqual)
    // fastqc(trimmomatic.out.paired_fq) // This will raise an error. Do you remember why?
}
