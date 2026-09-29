# Third-party software and models

The WordWatch MIT license covers its original application code only.

- NVIDIA NeMo-Speech.cpp 0.1.0: Apache-2.0; https://github.com/NVIDIA/NeMo-Speech.cpp. Its bundled dependencies and notices are preserved under `nvidia-runtime/share/licenses/nemo-speech`.
- NVIDIA Nemotron Speech Streaming English 0.6B Q8: NVIDIA Open Model License; https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b. Included model card and license under `nvidia-runtime/models` and `licenses`.
- NVIDIA Nemotron 3.5 ASR Streaming 0.6B Q8: OpenMDW 1.1; https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b. Included model card and license under `nvidia-runtime/models` and `licenses`.
- Vosk and vosk-model-small-en-us-0.15: Apache-2.0; https://alphacephei.com/vosk/models and https://github.com/alphacep/vosk-api. Model copyright 2020 Alpha Cephei Inc. Model files are unmodified.
- CUDA and Microsoft runtime components retain their respective vendor terms. CUDA license is included in `licenses`.
- Python, Tcl/Tk, NumPy, sounddevice/PortAudio, SoundFile/libsndfile, websockets, Vosk and their dependencies retain their respective licenses. See `licenses` and `Third-party-licenses.zip` in the installed folder. The archive also retains notices for components in earlier builds.

Model weights and runtime binaries are distributed as release assets, not committed to the source repository. Model file hashes are listed in `MODEL-SHA256SUMS.txt` in the installed folder.
