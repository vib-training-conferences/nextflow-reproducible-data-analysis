#!/usr/bin/env nextflow

// The input data is defined in the beginning.
params {
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/samplesheet.csv"
    outdir: String = "${launchDir}/results"
}

// Definition of a process
// A process being defined, does not mean it's invoked (see workflow)
process fastqc {
    publishDir {"${params.outdir}/quality-control-${sample}/"}, mode: 'copy', overwrite: true
    container 'quay.io/biocontainers/fastqc:0.11.9--0'
    
    input:
    tuple val(sample), path(reads)  // from is omitted

    output:
    path("*_fastqc.{zip,html}") 

    script:
    """
    fastqc ${reads}
    """
}

// Running a workflow with the defined processes here.  
workflow {
    log.info """\
        LIST OF PARAMETERS
    ================================
    Samplesheet      : ${params.samplesheet}
    Output-folder    : ${params.outdir}/
    """

    // Also channels are being created. 
    def read_pairs_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)] ) }
	    .view()

	fastqc(read_pairs_ch) 
}
