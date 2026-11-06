# Company Brain

[English](README.md) · **Español**

Asistente de IA privado para una consultora pequeña. La IA funciona entera en un MacBook Air M4 de 16 GB, sin servicios de IA en la nube, y responde solo con los documentos de la empresa, citando la fuente de cada dato.

Proyecto final del bootcamp de **AI Engineering de KeepCoding**. Diapositivas: [español](docs/presentation/company-brain-presentacion-es.pdf) · [inglés](docs/presentation/company-brain-presentation-en.pdf).

> Todos los clientes y documentos de este repositorio son **ficticios** o **públicos**. No contiene datos reales de clientes.

## El problema

Una consultora trabaja con transcripciones de llamadas, propuestas, contratos e informes. Son confidenciales, así que no pueden pegarse en ChatGPT ni en otros servicios de IA de internet. Los documentos siguen en el Google Drive de la empresa, como antes; Company Brain lee la copia sincronizada en el portátil y hace tres cosas sin enviar ningún documento a una IA externa:

| Modo | Qué hace |
| --- | --- |
| **Responder** | Responde preguntas sobre un cliente y cita el pasaje de origen. Si la respuesta no está en los documentos, lo dice. |
| **Resumir** | Resume una llamada (hasta una hora) y la descarga en Word. |
| **Redactar** | Redacta una propuesta con la metodología de la consultora y la descarga en PDF. |

## Restricciones

- 0 € extra en IA en la nube o APIs: la IA funciona en local.
- 16 GB de memoria (MacBook Air M4, el único ordenador disponible).
- Inglés y español.
- Pruebas solo con datos ficticios (60 documentos) y públicos (17 documentos).

## Arquitectura (RAG)

```
Al añadir un documento
  Carpeta de Drive ─▶ Docling (PDF, Word, fotos con OCR) ─▶ trozos de ~500 tokens
  ─▶ EmbeddingGemma 2 ─▶ PostgreSQL + pgvector

Al preguntar
  Búsqueda híbrida (vectores + palabras clave, Reciprocal Rank Fusion)
  ─▶ reranker mMiniLM ─▶ Qwen3.5-4B en Ollama ─▶ respuesta con citas
```

- **Modelo:** Qwen3.5-4B (Apache 2.0). Gemma 4 12B y Qwen3.5-9B se descartaron porque superaban el límite de memoria con el buscador cargado (`reports/benchmark_early.csv`).
- **Embeddings:** se compararon Qwen3-Embedding-0.6B, bge-m3 y EmbeddingGemma 2; empataron en calidad y EmbeddingGemma 2 fue el más rápido (`reports/tuning/`).
- **Reranker:** `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (118M parámetros, multilingüe), elegido en lugar de bge-reranker-v2-m3, que no cabía en 16 GB junto a los demás modelos.
- **Interfaz:** Open WebUI, con el asistente como modelo “Company Brain” (una Pipe que llama a la API local en `src/company_brain/backend.py`).
- **Fine-tuning:** pospuesto a propósito. Con menos de 50 documentos propios y 16 GB, RAG da más por menos coste.

## Evaluación

66 preguntas con respuesta conocida, mitad en inglés y mitad en español: 44 para ajustar (`eval/tuning.json`) y 22 cifradas hasta la prueba final, para no sobreajustar.

Métricas: recall@5 y MRR de la búsqueda, exactitud del dato, cita que lo respalda, negativa cuando no hay respuesta, resistencia a instrucciones trampa, y un juez LLM (Gemma 4 12B) para fidelidad y relevancia. Además, una valoración humana a ciegas de 20 respuestas.

Resultados. El conjunto de práctica se usó para ajustar el sistema; el examen final son las 22 preguntas bloqueadas, ejecutadas una sola vez el 6 de noviembre de 2026 con la configuración congelada (`config/selected.toml`, informe en `reports/final_eval.md`):

| Métrica | Práctica (44 preguntas) | Examen final (22 preguntas) | Objetivo |
| --- | --- | --- | --- |
| Preguntas superadas | 41 / 44 (93 %) | 16 / 22 (73 %) | – |
| Documento correcto entre los 5 primeros (recall@5) | 30 / 30 (100 %) | 15 / 16 (94 %) | ≥ 85 % |
| Ninguna cifra sin su fuente | 44 / 44 | 22 / 22 | 100 % |
| Dice “no lo sé” cuando no hay respuesta | 7 / 7 | 2 / 3 | ≥ 90 % |
| Dato correcto | 25 / 26 | 12 / 14 | – |
| Instrucciones trampa resistidas | 3 / 4 | 0 / 2 | – |
| Fidelidad a las fuentes (juez) | 0,89 | 0,80 | ≥ 0,90 |
| Relevancia de la respuesta (juez) | 0,71 | 0,70 | ≥ 0,80 |
| Bloqueos graves (mezcla de clientes, cifras sin fuente) | 0 | 0 | 0 |

Valoración humana a ciegas: 20 respuestas con una media de 4,8 sobre 5 (inglés 4,9, español 4,7).

Veredicto: **piloto, por debajo del objetivo.** Sirve para borradores que revisa una persona (nunca mostró una cifra sin su fuente ni mezcló clientes), todavía no para usarlo sin revisión. Los puntos débiles son la resistencia a instrucciones trampa, la relevancia (0,70) y la fidelidad de las respuestas en inglés.

El análisis está en [`notebooks/results.ipynb`](notebooks/results.ipynb) (en inglés) y todos los resultados en `reports/eval_runs.csv`. El conjunto de prueba está en Hugging Face: [AHAcosta/company-brain-eval](https://huggingface.co/datasets/AHAcosta/company-brain-eval).

## Privacidad y seguridad

- Un cortafuegos por aplicación (LuLu) impide que la IA salga a internet; se comprueba en cada arranque.
- Los documentos siguen en el Google Drive de la empresa; la IA solo lee la copia que la app de Drive sincroniza en el Mac. Las copias de seguridad van a Drive cifradas con `age`, así que el proveedor no puede leerlas.
- Copia de seguridad nocturna, cifrada y firmada, con restauración probada.
- Auditoría de seguridad OWASP (`reports/security_audit.md`).
- Solo datasets y documentos con licencia que permite uso comercial (`LICENCES.md`).

## Estructura

| Carpeta | Contenido |
| --- | --- |
| `src/company_brain/` | El asistente: ingesta, búsqueda, los tres modos, copias de seguridad, cortafuegos |
| `scripts/` | Evaluación, ajuste, datos de prueba y mantenimiento |
| `launchers/` | Botones de doble clic: Start AI, Stop AI, Check health… |
| `eval/` | Preguntas de evaluación y su esquema |
| `data/` | Hechos esperados y listas de documentos de prueba |
| `reports/` | Resultados de pruebas, benchmarks y auditoría |
| `tests/` | Tests unitarios y de integración (pytest) |
| `docs/` | Guía de uso, guion de la demo y registro de decisiones |
| `notebooks/` | Análisis de resultados |

## Cómo ejecutarlo

Requisitos: macOS en Apple Silicon, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com), [Postgres.app](https://postgresapp.com) con pgvector y Open WebUI.

```bash
uv sync
uv run pytest            # tests unitarios
```

Configura la carpeta de documentos en `config/sources.toml` y usa los lanzadores de `launchers/`. La guía completa está en `docs/USER_GUIDE.md`.

## Créditos

Los documentos ficticios son trabajo propio. Documentos públicos: CUAD (The Atticus Project, CC BY 4.0), CORD (NAVER Clova, CC BY 4.0), informe anual de la UK Statistics Authority (Open Government Licence v3.0) e informe anual 2024 del Ararteko (CC BY 4.0). Licencias de modelos y librerías en `LICENCES.md`.

## Licencia

Código: [MIT](LICENSE) © 2026 Ángeles Hernández Acosta. El dataset de prueba ficticio en Hugging Face es CC BY 4.0. Los modelos, librerías y documentos públicos mantienen sus propias licencias (`LICENCES.md`).
