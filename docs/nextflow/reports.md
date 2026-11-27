# Creating reports 
Nextflow has an embedded function for reporting a various information about the resources needed by each job and the timing. Just by adding a parameter on run-time, different kinds of reports can be created. 


1. **Workflow report**

    After running the nextflow pipeline script with the option `-with-report`, find the html report in the folder from where you launched the pipeline. 

    ```bash
    nextflow run exercises/05_reports/RNAseq.nf -with-report -profile docker
    ```
    
    This report describes the usage of resources and job durations and gives an indication of bottlenecks and possible optimizations in the pipeline. 

2. **DAG** 
    
    Use the option `-with-dag` to create a visualization of the workflow. By default and without any arguments, it will create a `.dot`-file that contains a description of the workflow, however to get a visualization we need to use an extra argument (e.g. `rnaseq.html`). This visualization is a nice overview of the workflow processes and how they are chained together and can be especially useful as a starting point to unravel more complex pipelines.

    ```bash
    nextflow run exercises/05_reports/RNAseq.nf -with-dag rnaseq.html -profile docker
    ```

    ```{note}
    As of Nextflow 22.04, the DAG can also be output in mermaid format, more information can be found [here](https://www.nextflow.io/docs/latest/tracing.html#dag-visualisation).
    ```

3. **Timeline Report**

    After running the nextflow pipeline script with the option `-with-timeline`, find the html report in the folder from where you launched the pipeline. 

    ```bash
    nextflow run exercises/05_reports/RNAseq.nf -with-timeline -profile docker
    ```
    
    This report summarizes the execution time of each process in your pipeline. It can be used to identify bottlenecks and to optimize the pipeline. More information about the formt of the timeline report can be found [here](https://www.nextflow.io/docs/latest/tracing.html#timeline-report).

4. **Seqera Platform**

    Adding the parameter `-with-tower` enables the Seqera Platform service and will output the reports to a browser-based platform. More about Seqera Platform [here](https://seqera.io/platform/)

---


````{tab} Exercise 1
Run the `RNAseq.nf` pipeline again, this time also make the reports (both html-report and a visualization of the pipeline)
```` 
````{tab} Solution 1
The command that we need for this is the following.
```bash
nextflow run exercises/05_reports/RNAseq.nf -profile docker -with-report -with-dag rnaseq.html 
```
To view the report and the dag, you will need to download the files to your local machine.
```` 
