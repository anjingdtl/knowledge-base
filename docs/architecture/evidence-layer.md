# Evidence Layer（原始证据层）

- 本地文档解析与增量索引  
- SQLite + FTS5 + sqlite-vec  
- Block 为最小引用单元（含 page/sheet/heading/line 定位）  
- **原始文档与 Block 是最终事实来源**；Wiki Claim 不得脱离 Evidence 成为第二真相  

相关代码：`block_store`、`hybrid_search`、`path_indexer`、`file_watcher`。
