# Firebase App Distribution — Kompletna instrukcja budowania i dystrybucji APK

Ten dokument opisuje krok po kroku, jak użyć **Firebase App Distribution** do budowania, podpisywania i dystrybucji plików APK (np. jako wrapper aplikacji mobilnej dla Twojego bota Telegram).

---

## Spis treści

1. [Wymagania wstępne](#1-wymagania-wstępne)
2. [Tworzenie projektu Firebase](#2-tworzenie-projektu-firebase)
3. [Konfiguracja aplikacji Android](#3-konfiguracja-aplikacji-android)
4. [Podłączenie Firebase SDK do projektu Android](#4-podłączenie-firebase-sdk-do-projektu-android)
5. [Budowanie APK (debug i release)](#5-budowanie-apk-debug-i-release)
6. [Podpisywanie APK (release keystore)](#6-podpisywanie-apk-release-keystore)
7. [Firebase App Distribution — konfiguracja](#7-firebase-app-distribution--konfiguracja)
8. [Dystrybucja APK przez Firebase CLI](#8-dystrybucja-apk-przez-firebase-cli)
9. [Dystrybucja APK przez Gradle plugin](#9-dystrybucja-apk-przez-gradle-plugin)
10. [Dystrybucja APK przez Firebase Console (ręcznie)](#10-dystrybucja-apk-przez-firebase-console-ręcznie)
11. [Automatyzacja z GitHub Actions](#11-automatyzacja-z-github-actions)
12. [Testowanie i weryfikacja](#12-testowanie-i-weryfikacja)
13. [Rozwiązywanie problemów](#13-rozwiązywanie-problemów)

---

## 1. Wymagania wstępne

### Narzędzia

| Narzędzie | Wersja min. | Instalacja |
|-----------|-------------|------------|
| Android Studio | 2023.1+ | [developer.android.com](https://developer.android.com/studio) |
| JDK | 17+ | `sudo apt install openjdk-17-jdk` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) |
| Firebase CLI | najnowsza | `npm install -g firebase-tools` |
| Konto Google | — | [console.firebase.google.com](https://console.firebase.google.com/) |

### Weryfikacja instalacji

```bash
# Sprawdź Java
java -version

# Sprawdź Android SDK
echo $ANDROID_HOME
sdkmanager --version

# Sprawdź Firebase CLI
firebase --version

# Zaloguj się do Firebase
firebase login
```

---

## 2. Tworzenie projektu Firebase

### Przez konsolę webową

1. Otwórz [console.firebase.google.com](https://console.firebase.google.com/)
2. Kliknij **„Dodaj projekt"** (Add project)
3. Podaj nazwę projektu (np. `gigagrok-app`)
4. Opcjonalnie włącz Google Analytics
5. Kliknij **„Utwórz projekt"** (Create project)

### Przez Firebase CLI

```bash
# Zaloguj się (jeśli jeszcze nie)
firebase login

# Utwórz nowy projekt
firebase projects:create gigagrok-app --display-name "GigaGrok App"

# Ustaw jako domyślny
firebase use gigagrok-app
```

---

## 3. Konfiguracja aplikacji Android

### Rejestracja aplikacji w Firebase

1. W konsoli Firebase → kliknij ikonę **Android** aby dodać aplikację
2. Wpisz **nazwę pakietu** (np. `pl.nexusoc.gigagrok`)
3. Opcjonalnie: podaj **nick** aplikacji i **SHA-1** klucza debugowego
4. Kliknij **„Zarejestruj aplikację"**
5. Pobierz plik **`google-services.json`**

### Uzyskanie SHA-1 (debug)

```bash
# Keystore debugowy (domyślny)
keytool -list -v -keystore ~/.android/debug.keystore -alias androiddebugkey -storepass android -keypass android

# Skopiuj SHA1 i wklej w konsoli Firebase
```

### Umieszczenie `google-services.json`

```
twoja-aplikacja/
├── app/
│   ├── google-services.json  ← TUTAJ
│   ├── src/
│   └── build.gradle
├── build.gradle
└── settings.gradle
```

---

## 4. Podłączenie Firebase SDK do projektu Android

### `build.gradle` (projekt — root level)

```groovy
// build.gradle (Project)
buildscript {
    repositories {
        google()
        mavenCentral()
    }
    dependencies {
        classpath 'com.android.tools.build:gradle:8.2.0'
        classpath 'com.google.gms:google-services:4.4.0'

        // Firebase App Distribution plugin
        classpath 'com.google.firebase:firebase-appdistribution-gradle:4.0.1'
    }
}
```

### `build.gradle` (moduł app)

```groovy
// build.gradle (Module: app)
plugins {
    id 'com.android.application'
    id 'com.google.gms.google-services'
    id 'com.google.firebase.appdistribution'
}

android {
    namespace 'pl.nexusoc.gigagrok'
    compileSdk 34

    defaultConfig {
        applicationId "pl.nexusoc.gigagrok"
        minSdk 24
        targetSdk 34
        versionCode 1
        versionName "1.0.0"
    }

    buildTypes {
        release {
            minifyEnabled true
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'

            firebaseAppDistribution {
                // Grupa testerów (nazwa z Firebase Console)
                groups = "beta-testers"
                // Notatki do wydania
                releaseNotes = "Nowa wersja — poprawki wydajności"
            }
        }
        debug {
            firebaseAppDistribution {
                groups = "internal-testers"
                releaseNotes = "Build debugowy"
            }
        }
    }
}

dependencies {
    // Firebase BOM (Bill of Materials) — zarządza wersjami
    implementation platform('com.google.firebase:firebase-bom:32.7.0')

    // Firebase Analytics (opcjonalnie)
    implementation 'com.google.firebase:firebase-analytics'

    // Firebase Crashlytics (opcjonalnie — raportowanie błędów)
    implementation 'com.google.firebase:firebase-crashlytics'
}
```

### Synchronizacja

W Android Studio: **File → Sync Project with Gradle Files**

---

## 5. Budowanie APK (debug i release)

### Debug APK

```bash
# Z katalogu projektu Android
cd twoja-aplikacja/

# Zbuduj debug APK
./gradlew assembleDebug

# APK znajduje się w:
# app/build/outputs/apk/debug/app-debug.apk
```

### Release APK

```bash
# Zbuduj release APK (wymaga podpisania — patrz sekcja 6)
./gradlew assembleRelease

# APK znajduje się w:
# app/build/outputs/apk/release/app-release.apk
```

### Bundle (AAB) — do Google Play

```bash
# Android App Bundle (preferowany przez Google Play)
./gradlew bundleRelease

# AAB znajduje się w:
# app/build/outputs/bundle/release/app-release.aab
```

---

## 6. Podpisywanie APK (release keystore)

### Tworzenie klucza (jednorazowo)

```bash
keytool -genkey -v \
  -keystore gigagrok-release.keystore \
  -alias gigagrok-key \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000 \
  -storepass TWOJE_HASLO \
  -keypass TWOJE_HASLO \
  -dname "CN=GigaGrok, OU=Dev, O=NexusOC, L=Warsaw, ST=Mazovia, C=PL"
```

> ⚠️ **WAŻNE:** Nigdy nie commituj keystore ani haseł do repozytorium!

### Konfiguracja podpisywania w `build.gradle`

```groovy
android {
    signingConfigs {
        release {
            storeFile file('../gigagrok-release.keystore')
            storePassword System.getenv('KEYSTORE_PASSWORD') ?: ''
            keyAlias 'gigagrok-key'
            keyPassword System.getenv('KEY_PASSWORD') ?: ''
        }
    }

    buildTypes {
        release {
            signingConfig signingConfigs.release
            minifyEnabled true
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }
    }
}
```

### Zmienne środowiskowe (bezpieczne przechowywanie haseł)

```bash
# Dodaj do ~/.bashrc lub ~/.zshrc
export KEYSTORE_PASSWORD="twoje_bezpieczne_haslo"
export KEY_PASSWORD="twoje_bezpieczne_haslo"

# Lub użyj pliku local.properties (nie commituj!)
echo "KEYSTORE_PASSWORD=twoje_haslo" >> local.properties
echo "KEY_PASSWORD=twoje_haslo" >> local.properties
```

---

## 7. Firebase App Distribution — konfiguracja

### Włączenie App Distribution

1. W konsoli Firebase → **App Distribution** (menu boczne)
2. Kliknij **„Rozpocznij"** (Get started)
3. Utwórz **grupy testerów**:
   - `internal-testers` — zespół deweloperski
   - `beta-testers` — testerzy zewnętrzni

### Dodawanie testerów

1. **Firebase Console** → App Distribution → **Testerzy i grupy**
2. Kliknij **„Dodaj testerów"**
3. Wpisz adresy e-mail (jeden na linię)
4. Przypisz do grupy

### Przez CLI

```bash
# Dodaj testerów do grupy
firebase appdistribution:testers:add \
  --emails "tester1@example.com,tester2@example.com" \
  --group-alias "beta-testers" \
  --project gigagrok-app
```

---

## 8. Dystrybucja APK przez Firebase CLI

### Instalacja i logowanie

```bash
# Instalacja Firebase CLI
npm install -g firebase-tools

# Logowanie
firebase login

# Weryfikacja
firebase projects:list
```

### Dystrybucja APK

```bash
# Upload APK do App Distribution
firebase appdistribution:distribute app/build/outputs/apk/release/app-release.apk \
  --app YOUR_FIREBASE_APP_ID \
  --groups "beta-testers" \
  --release-notes "v1.0.0 — pierwsza wersja beta"
```

> **YOUR_FIREBASE_APP_ID** znajdziesz w:
> Firebase Console → Ustawienia projektu → Ogólne → Identyfikator aplikacji
> Format: `1:123456789:android:abc123def456`

### Pełny przykład (ze zmiennymi)

```bash
#!/bin/bash
# deploy-apk.sh

APP_ID="1:123456789012:android:abc123def456ghi789"
APK_PATH="app/build/outputs/apk/release/app-release.apk"
GROUPS="beta-testers"
VERSION=$(grep versionName app/build.gradle | awk '{print $2}' | tr -d '"')

echo "📱 Dystrybuję APK v${VERSION}..."

firebase appdistribution:distribute "$APK_PATH" \
  --app "$APP_ID" \
  --groups "$GROUPS" \
  --release-notes "v${VERSION} — $(date +%Y-%m-%d)"

echo "✅ APK wysłany do testerów!"
```

---

## 9. Dystrybucja APK przez Gradle plugin

### Konfiguracja w `build.gradle`

(Patrz sekcja 4 — plugin jest już dodany)

### Autoryzacja

```bash
# Opcja 1: Firebase login (interaktywne)
firebase login

# Opcja 2: Service Account (CI/CD)
export FIREBASE_TOKEN=$(firebase login:ci)

# Opcja 3: Service Account JSON (zalecane dla CI)
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
```

### Dystrybucja z Gradle

```bash
# Debug APK → grupa internal-testers
./gradlew assembleDebug appDistributionUploadDebug

# Release APK → grupa beta-testers
./gradlew assembleRelease appDistributionUploadRelease
```

### Konfiguracja dynamicznych notatek

```groovy
// build.gradle (Module: app)
firebaseAppDistribution {
    groups = "beta-testers"
    releaseNotesFile = "release-notes.txt"
    // lub:
    // releaseNotes = "Automatyczny build z CI"
}
```

---

## 10. Dystrybucja APK przez Firebase Console (ręcznie)

1. Otwórz [Firebase Console](https://console.firebase.google.com/)
2. Wybierz projekt → **App Distribution**
3. Kliknij **„Nowe wydanie"** (New release)
4. Przeciągnij plik APK lub kliknij **„Przeglądaj"**
5. Wpisz **notatki do wydania**
6. Wybierz **grupy testerów**
7. Kliknij **„Dystrybuuj"** (Distribute)

Testerzy otrzymają e-mail z linkiem do instalacji.

---

## 11. Automatyzacja z GitHub Actions

### Plik workflow: `.github/workflows/firebase-distribute.yml`

```yaml
name: Build & Distribute APK

on:
  push:
    tags:
      - 'v*'  # Uruchom przy tagach wersji (v1.0.0, v1.1.0, etc.)
  workflow_dispatch:  # Ręczne uruchomienie

jobs:
  build-and-distribute:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout kodu
        uses: actions/checkout@v4

      - name: Setup JDK 17
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'

      - name: Cache Gradle
        uses: actions/cache@v4
        with:
          path: |
            ~/.gradle/caches
            ~/.gradle/wrapper
          key: gradle-${{ runner.os }}-${{ hashFiles('**/*.gradle*', '**/gradle-wrapper.properties') }}

      - name: Dekoduj keystore
        run: |
          echo "${{ secrets.KEYSTORE_BASE64 }}" | base64 -d > app/gigagrok-release.keystore

      - name: Zbuduj release APK
        env:
          KEYSTORE_PASSWORD: ${{ secrets.KEYSTORE_PASSWORD }}
          KEY_PASSWORD: ${{ secrets.KEY_PASSWORD }}
        run: |
          chmod +x gradlew
          ./gradlew assembleRelease

      - name: Upload do Firebase App Distribution
        uses: wzieba/Firebase-Distribution-Github-Action@v1
        with:
          appId: ${{ secrets.FIREBASE_APP_ID }}
          serviceCredentialsFileContent: ${{ secrets.FIREBASE_SERVICE_ACCOUNT }}
          groups: beta-testers
          file: app/build/outputs/apk/release/app-release.apk
          releaseNotes: |
            Wersja: ${{ github.ref_name }}
            Commit: ${{ github.sha }}
            Data: ${{ github.event.head_commit.timestamp }}

      - name: Upload APK jako artifact
        uses: actions/upload-artifact@v4
        with:
          name: release-apk
          path: app/build/outputs/apk/release/app-release.apk
```

### Wymagane GitHub Secrets

| Secret | Opis | Jak uzyskać |
|--------|------|-------------|
| `FIREBASE_APP_ID` | ID aplikacji Firebase | Firebase Console → Ustawienia → Ogólne |
| `FIREBASE_SERVICE_ACCOUNT` | JSON service account | Firebase Console → Ustawienia → Konta usługi → Generuj nowy klucz prywatny |
| `KEYSTORE_BASE64` | Keystore w base64 | `base64 gigagrok-release.keystore` |
| `KEYSTORE_PASSWORD` | Hasło do keystore | Twoje hasło |
| `KEY_PASSWORD` | Hasło do klucza | Twoje hasło |

### Kodowanie keystore do base64

```bash
# Zakoduj keystore (wynik wklej do GitHub Secret)
base64 -w 0 gigagrok-release.keystore
```

---

## 12. Testowanie i weryfikacja

### Weryfikacja APK

```bash
# Sprawdź podpis APK
apksigner verify --print-certs app/build/outputs/apk/release/app-release.apk

# Sprawdź zawartość APK
aapt dump badging app/build/outputs/apk/release/app-release.apk | head -5
```

### Weryfikacja dystrybucji

```bash
# Lista wydań w App Distribution
firebase appdistribution:releases:list --app YOUR_FIREBASE_APP_ID
```

### Instalacja przez testerów

1. Tester otrzymuje **e-mail** od Firebase
2. Klika link → instaluje **Firebase App Tester** (jeśli nie ma)
3. Akceptuje zaproszenie
4. Pobiera i instaluje APK

> ⚠️ Na urządzeniu Android: **Ustawienia → Bezpieczeństwo → Nieznane źródła** musi być włączone.

---

## 13. Rozwiązywanie problemów

### Problem: `Unable to process the APK`

```
✅ Upewnij się, że APK jest prawidłowo podpisany
✅ Sprawdź minSdk (nie mniejszy niż 21)
✅ Sprawdź, czy plik nie jest uszkodzony: unzip -t app-release.apk
```

### Problem: `App ID not found`

```
✅ Sprawdź FIREBASE_APP_ID w Firebase Console → Ustawienia → Ogólne
✅ Format: 1:XXXXXXXXXXXX:android:XXXXXXXXXXXXXX
✅ Upewnij się, że appId odpowiada platformie (android, nie ios)
```

### Problem: `Permission denied` przy upload

```
✅ Sprawdź, czy service account ma rolę "Firebase App Distribution Admin"
✅ W Google Cloud Console → IAM → dodaj rolę do service account
✅ Lub: firebase login:ci i użyj FIREBASE_TOKEN
```

### Problem: Tester nie otrzymuje e-maila

```
✅ Sprawdź folder SPAM
✅ Upewnij się, że e-mail jest dodany do grupy testerów
✅ Spróbuj: firebase appdistribution:testers:add --emails "email@example.com"
```

### Problem: `Build failed` w GitHub Actions

```
✅ Sprawdź logi: Actions → workflow run → kliknij na krok z błędem
✅ Upewnij się, że secrets są poprawnie ustawione
✅ Upewnij się, że keystore jest zakodowany w base64 bez przełamań linii
✅ Sprawdź wersję Gradle i JDK
```

---

## Podsumowanie

| Krok | Komenda / Akcja |
|------|-----------------|
| **1. Zbuduj APK** | `./gradlew assembleRelease` |
| **2. Upload CLI** | `firebase appdistribution:distribute app.apk --app ID --groups testers` |
| **3. Upload Gradle** | `./gradlew appDistributionUploadRelease` |
| **4. Upload Console** | Firebase Console → App Distribution → Upload |
| **5. CI/CD** | GitHub Actions + `wzieba/Firebase-Distribution-Github-Action` |

> 📌 **Tip:** Używaj tagów Git (`v1.0.0`, `v1.1.0`) do automatycznego triggera workflow — każdy tag = nowe wydanie w Firebase App Distribution.
