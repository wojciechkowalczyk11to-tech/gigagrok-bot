# Google Drive → Grok Collection Pipeline

Zestaw skryptów do eksportu plików developerskich z Google Drive, organizacji ich
w kategorie, ekstrakcji kodu źródłowego, i uploadu do kolekcji xAI Grok API.

## Pipeline (2 kroki)

### Krok 1: Eksport z Google Drive

```bash
pip install -r scripts/requirements-gdrive.txt
python scripts/gdrive_to_collection.py [opcje]
```

**Opcje:**

| Flaga               | Env Variable             | Opis                                      |
|---------------------|--------------------------|--------------------------------------------|
| `--service-account` | `GDRIVE_SERVICE_ACCOUNT` | Ścieżka do JSON klucza service account    |
| `--credentials`     | —                        | Ścieżka do OAuth2 credentials.json        |
| `--root-folder-id`  | `GDRIVE_ROOT_FOLDER_ID`  | ID konkretnego folderu (domyślnie: cały Drive) |
| `--output-dir`      | `GDRIVE_OUTPUT_DIR`      | Katalog wyjściowy (domyślnie: `./gdrive_export`) |
| `--max-file-size`   | —                        | Maks. rozmiar pliku w MB (domyślnie: 10)  |
| `--dry-run`         | —                        | Tylko listuj pliki bez pobierania          |
| `--verbose`         | —                        | Logowanie DEBUG                            |

**Co robi skrypt:**
1. Łączy się z Google Drive (OAuth2 lub service account)
2. Skanuje rekurencyjnie wszystkie pliki
3. Filtruje: pomija obrazy, video, audio, archiwa, pliki Office, binarki
4. Pobiera tylko pliki developerskie (kod, config, docs)
5. Organizuje w katalogi wg kategorii:
   - `python/`, `javascript/`, `typescript/`, `web/`, `config/`
   - `shell/`, `database/`, `docker/`, `notebooks/`
   - `java/`, `go/`, `rust/`, `cpp/`, `csharp/`, `ruby/`, `php/`
   - `swift/`, `r_lang/`, `lua/`, `perl/`, `elixir/`, `scala/`
   - `haskell/`, `dart/`, `terraform/`, `proto/`
   - `markdown_docs/`, `xml/`, `other_dev/`
6. Z notebooków `.ipynb` wyciąga tylko komórki z kodem (zapisuje jako `.py`)
7. Generuje `manifest.json` i `summary.json`

### Krok 2: Upload do kolekcji Grok API

```bash
export XAI_API_KEY=your_key
export XAI_COLLECTION_ID=collection_xxx
python scripts/upload_to_collection.py [opcje]
```

**Opcje:**

| Flaga              | Env Variable         | Opis                                    |
|--------------------|----------------------|------------------------------------------|
| `--input-dir`      | `GDRIVE_OUTPUT_DIR`  | Katalog z wyeksportowanymi plikami       |
| `--collection-id`  | `XAI_COLLECTION_ID`  | ID kolekcji xAI                          |
| `--api-key`        | `XAI_API_KEY`        | Klucz API xAI                            |
| `--base-url`       | `XAI_BASE_URL`       | Base URL (domyślnie: https://api.x.ai/v1) |
| `--dry-run`        | —                    | Tylko listuj pliki bez uploadu            |
| `--verbose`        | —                    | Logowanie DEBUG                           |

## Uwierzytelnianie Google Drive

### Opcja A: Service Account (rekomendowane dla VM)
1. W Google Cloud Console → IAM & Admin → Service Accounts → Create
2. Nadaj uprawnienia do Drive (Domain-wide delegation lub udostępnij foldery)
3. Pobierz klucz JSON
4. Ustaw `GDRIVE_SERVICE_ACCOUNT=/path/to/key.json`

### Opcja B: OAuth2 (dla lokalnego użycia)
1. W Google Cloud Console → APIs → Credentials → OAuth 2.0 Client ID → Desktop App
2. Pobierz `credentials.json` do katalogu roboczego
3. Przy pierwszym uruchomieniu otworzy się przeglądarka do logowania
4. Token zostanie zapisany w `token.json`

## Przykład pełnego pipeline

```bash
# 1. Eksport z konkretnego folderu
python scripts/gdrive_to_collection.py \
  --service-account ~/gdrive-key.json \
  --root-folder-id 1ABCdef_ghijklmno \
  --output-dir ./gdrive_export

# 2. Przegląd (opcjonalny)
cat ./gdrive_export/summary.json
ls -la ./gdrive_export/*/

# 3. Upload do kolekcji
python scripts/upload_to_collection.py \
  --input-dir ./gdrive_export \
  --collection-id collection_xxx \
  --api-key $XAI_API_KEY
```
