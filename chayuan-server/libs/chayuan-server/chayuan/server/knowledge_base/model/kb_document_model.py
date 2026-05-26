from langchain_core.documents import Document


class DocumentWithVSId(Document):
    """
    矢量化后的文档
    """

    score: float = 3.0
