#!/usr/bin/env nextflow


params {
    // set default input parameters (these can be altered by calling their flag on the command line, e.g., nextflow run main.nf --reads 'data2/*_R{1,2}.fastq')
    samplesheet: Path = "${launchDir}/samplesheet_project.csv"
    outdir: String = "${launchDir}/output"
    fw_primer: String = "GTGCCAGCAGCCGCGGTAA"
    rv_primer: String = "GGACTACACGGGTTTCTAAT"
    
    //set the path to the script to run in the DADA2 process (you can also make a folder 'bin' and put this script in there so it will automatically be added to nextflow's path)
    script1: Path = "${projectDir}/reads2counts.r"
}

// include processes and subworkflows to make them available for use in this script 
include { check_QC as check_QC_raw; check_QC as check_QC_trimmed } from "./modules/QC" 
include { FASTP } from "./modules/trimming"
include { DADA2 } from "./modules/reads2counts"


workflow {
    // Set a header made using https://patorjk.com/software/taag (but be sure to escape characters such as dollar signs and backslashes, e.g., '$'=> '\$' and '\' =>'\\')
    log.info """
    ==============================================================================================

                                            \$\$\\                     \$\$\\ \$\$\\                     
                                            \\__|                    \$\$ |\\__|                    
    \$\$\$\$\$\$\\\$\$\$\$\\  \$\$\\   \$\$\\        \$\$\$\$\$\$\\  \$\$\\  \$\$\$\$\$\$\\   \$\$\$\$\$\$\\  \$\$ |\$\$\\ \$\$\$\$\$\$\$\\   \$\$\$\$\$\$\\  
    \$\$  _\$\$  _\$\$\\ \$\$ |  \$\$ |      \$\$  __\$\$\\ \$\$ |\$\$  __\$\$\\ \$\$  __\$\$\\ \$\$ |\$\$ |\$\$  __\$\$\\ \$\$  __\$\$\\ 
    \$\$ / \$\$ / \$\$ |\$\$ |  \$\$ |      \$\$ /  \$\$ |\$\$ |\$\$ /  \$\$ |\$\$\$\$\$\$\$\$ |\$\$ |\$\$ |\$\$ |  \$\$ |\$\$\$\$\$\$\$\$ |
    \$\$ | \$\$ | \$\$ |\$\$ |  \$\$ |      \$\$ |  \$\$ |\$\$ |\$\$ |  \$\$ |\$\$   ____|\$\$ |\$\$ |\$\$ |  \$\$ |\$\$   ____|
    \$\$ | \$\$ | \$\$ |\\\$\$\$\$\$\$\$ |      \$\$\$\$\$\$\$  |\$\$ |\$\$\$\$\$\$\$  |\\\$\$\$\$\$\$\$\\ \$\$ |\$\$ |\$\$ |  \$\$ |\\\$\$\$\$\$\$\$\\ 
    \\__| \\__| \\__| \\____\$\$ |      \$\$  ____/ \\__|\$\$  ____/  \\_______|\\__|\\__|\\__|  \\__| \\_______|
                  \$\$\\   \$\$ |      \$\$ |          \$\$ |                                            
                  \\\$\$\$\$\$\$  |      \$\$ |          \$\$ |                                            
                   \\______/       \\__|          \\__|                                                  
    
    ==============================================================================================

    INPUT PARAMETERS:
        - samplesheet : ${params.samplesheet}
        - output directory : ${params.outdir}
        - forward primer sequence : ${params.fw_primer}
        - reverse primer sequence : ${params.rv_primer}

    ==============================================================================================
    """.stripIndent()

    // set input data
    def pe_reads_ch = channel.fromPath( params.samplesheet, checkIfExists: true )
        .splitCsv(header:true)
        .map{ row -> tuple( row.sample, [file(row.fastq_1, checkIfExists: true), file(row.fastq_2, checkIfExists: true)] ) }

    //pass the 'step' and the raw reads to the QC subworkflow
    check_QC_raw("raw", pe_reads_ch)
    
    // the "raw" notation creates a value channel. This is equivalent to the following lines
    // step1 = channel.value("raw")
    // check_QC_raw(step1, pe_reads_ch)

    //pass the raw reads and the primer sequences to the fastp process
    FASTP(pe_reads_ch, params.fw_primer, params.rv_primer)

    //pass the 'step' and the trimmed reads to the QC subworkflow
    check_QC_trimmed("trimmed", FASTP.out)
    
    //pass the paths to the reads to the DADA2 process
    def dada2_input = FASTP.out
        .map{_sample, reads -> reads}
        .collect()

    // you could also add the closure to the collect operator to do this in one step
    // dada2_input = FASTP.out
    //     .collect{x -> x[1]}

    DADA2(dada2_input)

    workflow.onComplete = {
        println "Pipeline completed at: ${workflow.complete}"
        println "Time to complete workflow execution: ${workflow.duration}"
        println "Execution status: ${workflow.success ? 'Succesful' : 'Failed' }"
    }

    workflow.onError = {
        println "Oops... Pipeline execution stopped with the following message: ${workflow.errorMessage}"
    }

}
