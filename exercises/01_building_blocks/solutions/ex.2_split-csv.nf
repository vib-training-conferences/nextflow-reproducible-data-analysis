#!/usr/bin/env nextflow

params {
    input_csv: Path = 'exercises/01_building_blocks/input.csv'
}

process release_info {
    debug true

    input:
    tuple val(boardgame), val(release_year)  

    script:
    """
    echo $boardgame released in $release_year
    """
}

workflow {
    def games_ch = channel
                    .fromPath(params.input_csv)
                    .splitCsv(header:true)
                    .map{ row-> tuple(row.boardgame, row.release_year) }

    games_ch.view()
    release_info(games_ch)
}