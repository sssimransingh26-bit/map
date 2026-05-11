# Advanced Image Forensics & Geolocation Verification System

## Overview
This project is a multi-module **image forensics and geolocation verification system** built using Python. It analyzes uploaded images to detect tampering, AI generation, metadata inconsistencies, and extracts geolocation information from EXIF data to visualize image location on an interactive map.

It combines **computer vision, metadata analysis, and geospatial visualization** into a single Flask-based web application.

---

## 🚀 Features

### 🧾 Metadata & EXIF Analysis
- Extracts EXIF data from images
- Detects timestamp inconsistencies between file system and EXIF data
- Flags suspicious camera/software metadata
- Identifies missing or fake GPS information

---

### 🧠 Image Forensics
- **Error Level Analysis (ELA)** to highlight manipulated regions
- **Noise inconsistency detection** using block-based statistical analysis
- **Clone (copy-move) detection** using image hashing
- **Edge artifact detection** using Canny edge analysis

---

### 🤖 AI-Generated Image Detection
- Detects known AI tools (Stable Diffusion, Midjourney, DALL·E, etc.)
- Identifies AI prompt artifacts in metadata (e.g., `negative_prompt`, `steps`)
- Flags images with missing metadata + high resolution as suspicious

---

### 👤 Face Detection
- Detects number of human faces using OpenCV Haar cascades

---

### 🌍 Geolocation Mapping
- Extracts GPS coordinates from EXIF metadata
- Converts DMS (Degrees/Minutes/Seconds) → Decimal format
- Displays image location on an **interactive Folium map**

---

## 🛠️ Tech Stack

- Python 🐍
- Flask 🌐
- OpenCV 👁️
- NumPy 🔢
- Pillow (PIL) 🖼️
- ExifRead 📷
- scikit-image 🔬
- imagehash 🔐
- Folium 🌍

---

## 📂 Project Structure
