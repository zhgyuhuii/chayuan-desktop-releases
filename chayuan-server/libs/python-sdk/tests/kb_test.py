import logging

from open_chatcaht.chayuan_api import Chayuan
from open_chatcaht.types.knowledge_base.doc.upload_temp_docs_param import UploadTempDocsParam

# client = Chayuan()
# print('create_kb', client.knowledge.create_kb(knowledge_base_name="example_kb"))
# print('update_kb_info', client.knowledge.update_kb_info(knowledge_base_name="example_kb", kb_info='aaaaaaa'))
# print('list_kb', client.knowledge.list_kb())
# print('list_kb_docs_file', client.knowledge.list_kb_docs_file(knowledge_base_name="samples"))
# print('delete_kb', client.knowledge.delete_kb(knowledge_base_name="example_kb"))
# print('search_kb_docs', client.knowledge.search_kb_docs(knowledge_base_name="example_kb", query="hello"))
# print('upload_kb_docs', client.knowledge.upload_kb_docs(
#     files=["data/upload_file1.txt", "data/upload_file2.txt"],
#     knowledge_base_name="example_kb",
# ))
# print('search_kb_docs', client.knowledge.search_kb_docs(knowledge_base_name="example_kb", query="hello"))
# print('recreate_vector_store', client.knowledge.recreate_vector_store(
#     knowledge_base_name="samples",
# ))
# print('recreate_summary_vector_store', client.knowledge.recreate_summary_vector_store(
#     knowledge_base_name="example_kb",
#     embed_model="embedding-2",
#     model_name="glm-4",
# ))
# for data in client.knowledge.summary_file_to_vector_store(
#         knowledge_base_name="samples",
#         file_name="data/upload_file1.txt",
#         embed_model="embedding-2",
#         max_tokens=10000):
#     print(data)
# print('summary_file_to_vector_store', client.knowledge.summary_doc_ids_to_vector_store(
#     knowledge_base_name="samples",
#     file_name="data/upload_file1.txt",
# ))
# print('delete_kb_docs', client.knowledge.delete_kb_docs(
#     knowledge_base_name="samples",
#     file_names=["upload_file1.txt"],
# ))

# print(client.knowledge.download_kb_doc_file(
#     knowledge_base_name='example_kb',
#     file_name='README.md'
# ))
# print(client.knowledge.kb_doc_file_content(
#     knowledge_base_name='example_kb',
#     file_name='README.md'
# ))
# print(client.knowledge.upload_temp_docs(
#     files=["README.md", ],
#     knowledge_id="4",
# ))
# print(client.knowledge.search_temp_kb_docs(knowledge_id="cf414f74bca24fbdaece1ae8bb4d3970", query="hello"))
