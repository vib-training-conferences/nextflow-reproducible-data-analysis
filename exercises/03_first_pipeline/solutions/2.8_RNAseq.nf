#!/usr/bin/env nextflow

params {
    // General parameters
    datadir: Path = "${launchDir}/data"
    outdir: String = "${launchDir}/results"

    // Input parameters
    samplesheet: Path = "${launchDir}/exercises/03_first_pipeline/samplesheet.csv"
    genome: Path = "${launchDir}/data/ggal_1_48850000_49020000.Ggal71.500bpflank.fa"
    gtf: Path = "${launchDir}/data/ggal_1_48850000_49020000.bed.gff"

    // Trimmomatic
    slidingwindow: String = "SLIDINGWINDOW:4:15"
    avgqual: String = "AVGQUAL:30"

    // Star
    genomeSAindexNbases: Integer = 10
    lengthreads: Integer = 98
}


include { fastqc as fastqc_raw; fastqc as fastqc_trim } from "../../../modules/fastqc" //addParams(OUTPUT: fastqcOutputFolder)
include { trimmomatic } from "../../../modules/trimmomatic"
include { star_idx; star_alignment } from "./star"
include { multiqc } from "../../../modules/multiqc"

// Running a workflow with the defined processes here.  
workflow {
    log.info """\
        LIST OF PARAMETERS
    ================================
                GENERAL
    Data-folder      : ${params.datadir}
    Results-folder   : ${params.outdir}
    ================================
        INPUT & REFERENCES 
    Samplesheet      : ${params.samplesheet}
    Reference genome : ${params.genome}
    GTF-file         : ${params.gtf}
    ================================
            TRIMMOMATIC
    Sliding window   : ${params.slidingwindow}
    Average quality  : ${params.avgqual}
    ================================
                STAR
    Length-reads     : ${params.lengthreads}
    SAindexNbases    : ${params.genomeSAindexNbases}
    ================================
    """

    // Channels are being created. 
    def read_pairs_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)] ) }

    def genome = channel.fromPath(params.genome)
    def gtf = channel.fromPath(params.gtf) 

    // QC on raw reads
    fastqc_raw(read_pairs_ch) 
    
    // Trimming & QC
    trimmomatic(read_pairs_ch, params.slidingwindow, params.avgqual)
    fastqc_trim(trimmomatic.out.trim_fq)
    
    // Create index
    star_idx(genome, gtf, params.genomeSAindexNbases)

    // Combine channels
    def alignment_input = trimmomatic.out.trim_fq
      .combine(star_idx.out.index)
      .combine(gtf)

    alignment_input.view()
    
    // Mapping 
    star_alignment(alignment_input)
    
    // Multi QC on all results
    def multiqc_input = fastqc_raw.out.fastqc_out
      .mix(fastqc_trim.out.fastqc_out)
      .collect()

    multiqc(multiqc_input)
}


// alternative solution using value channels instead:

include { star_alignment as star_alignment_alt_sol } from "../../../modules/star"
workflow alternative_solution {
    // def read_pairs_ch_alt = Channel
    //   .fromFilePairs(params.reads, checkIfExists:true)
    def read_pairs_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1), file(row.fastq_2)] ) }

    def genome_alt = channel.value(file(params.genome))
    def gtf_alt = channel.value(file(params.gtf))

    // QC on raw reads
    fastqc_raw(read_pairs_ch) 
        
    // Trimming & QC
    trimmomatic(read_pairs_ch, params.slidingwindow, params.avgqual)
    fastqc_trim(trimmomatic.out.trim_fq)

    star_idx(genome_alt, gtf_alt, params.genomeSAindexNbases)
    star_alignment_alt_sol(trimmomatic.out.trim_fq, star_idx.out.index, gtf_alt)
}
