process fastqc {
  publishDir {"${params.outdir}/quality-control-${sample}/"}, mode: 'copy', overwrite: true
  label 'low'
  container 'quay.io/biocontainers/fastqc:0.11.9--0'
  
  input:
  tuple val(sample), path(reads)

  output:
  path("*_fastqc.{zip,html}"), emit: fastqc_out

  script:
  """
  fastqc ${reads}
  """
}

