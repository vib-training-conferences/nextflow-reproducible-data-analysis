process bowtie_idx {
    publishDir {"${params.refdir}/bt2idx/"}, mode: 'copy', pattern: "*.bt2"  
    label 'high'
    container "quay.io/biocontainers/bowtie2:2.2.5--py38h8c62d01_8"

    input:
    path genome
    
    output:
    tuple val("${params.idxname}"), path("${params.idxname}*"), emit: index

    // tuple val("${ref}"), path ("${ref}*.ebwt")

    script:
    """
    bowtie2-build ${genome} ${params.idxname}
    """
}

process bowtie_alignment {
    publishDir {"${params.outdir}/mapped-reads/"}, mode: 'copy', overwrite: true  //, pattern: "*.bam"  
    label 'high'
    container "quay.io/biocontainers/bowtie2:2.2.5--py38h8c62d01_8"

    input:
    tuple val(sample), path(reads) 
    tuple val(bt2idx), path(bt2idx_files)

    output:
    path("${sample}.sam"), emit: aligend_reads_sam

    script:
    """
    bowtie2 \\
        --dovetail \\
        --phred33 \\
        -x ${bt2idx} \\
        -1 ${reads[0]} \\
        -2 ${reads[1]} \\
        -S ${sample}.sam
    """
    // Pipe to: (but need one container with both bowtie and samtools in it then)
    // samtools view -bS - > ${outdir}/mapped-reads/${sample}_aligned_reads.bam
}


