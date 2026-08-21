"""Gerador de arquivos TXT para inscrição em lote no Sistema Enade.

Suporta as três categorias previstas nos editais anuais do INEP:

- Concluintes (Anexo VII — identificador de arquivo N99)
- Ingressantes (Anexo VIII — identificador de arquivo N92)
- Estudantes do 4º ano de Medicina (Anexo VI — identificador N99M)

O ano de referência das validações é configurado em tempo de execução,
permitindo o uso do aplicativo em qualquer edição do Enade.
"""

__version__ = "2.0.0"
