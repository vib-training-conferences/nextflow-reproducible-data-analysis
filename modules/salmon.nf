process salmon_idx {
    label 'med'
    container 'biocontainers/salmon:v0.12.0ds1-1b1-deb_cv1'

    input:
    path transcriptome 

    output:
    path 'index'

    script:
    """
    salmon index --threads ${task.cpus} -t ${transcriptome} -i index
    """
}

process salmon_quant {
    publishDir {"${params.outdir}/mapped-reads/"}, mode: 'copy', overwrite: true
    label 'med'
    container 'biocontainers/salmon:v0.12.0ds1-1b1-deb_cv1'

    input:
    path index 
    tuple val(pair_id), path(reads) 

    output:
    path pair_id, emit: quant_out 

    script:
    """
    salmon quant --threads ${task.cpus} --libType=U -i ${index} -1 ${reads[0]} -2 ${reads[1]} -o ${pair_id}
    """
}

