# 原始数据

通过 `python src/download_data.py` 从UCI官方入口下载并解压。
原始ZIP、Excel在本地保留，不提交Git；manifest.json记录本次分析文件摘要，uci_metadata.json保留来源说明。

引用：Chen, D. (2015). Online Retail. UCI Machine Learning Repository. https://doi.org/10.24432/C5BW33

元数据标注无缺失，但实际原始Excel中有客户ID及描述缺失；分析按文件实测处理。
