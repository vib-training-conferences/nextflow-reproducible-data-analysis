process multiqc {
    publishDir({"${params.outdir}/multiqc/"}, mode: 'copy', overwrite: true)
    label 'low'
    container 'quay.io/biocontainers/multiqc:1.9--py_1'

    input:
    path (inputfiles)

    output:
    path "multiqc_report.html"					

    script:
    """
    multiqc .
    """
}

