# rebble-asr
asr.rebble.io: speech recognition for rebble

## Overview

Rebble ASR provides automatic speech recognition services for Pebble smartwatches.

## Configuration Options

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `ASR_API_KEY` | API key for ElevenLabs or Groq | None | Required for cloud providers |
| `ASR_API_PROVIDER` | Speech recognition provider (`elevenlabs`, `groq`, `wyoming-whisper`) | None | Yes |
| `WYOMING_HOST` | Host address for Wyoming service | `localhost` | Required for wyoming-whisper |
| `WYOMING_PORT` | Port for Wyoming service | `10300` | Required for wyoming-whisper |
| `DEBUG` | Enable detailed debug logging | `false` | No |

### ASR Providers

#### ElevenLabs

Uses ElevenLabs' Scribe v1 model for high-quality transcription.

```bash
export ASR_API_PROVIDER=elevenlabs
export ASR_API_KEY=your_elevenlabs_api_key
```

#### Groq

Uses Groq API with Whisper model for fast transcription.

```bash
export ASR_API_PROVIDER=groq
export ASR_API_KEY=your_groq_api_key
```

#### Wyoming-Whisper

Uses a local Wyoming-compatible speech recognition service (like [Home Assistant's Whisper](https://hub.docker.com/r/rhasspy/wyoming-whisper) integration).

```bash
export ASR_API_PROVIDER=wyoming-whisper
export WYOMING_HOST=your_wyoming_host  # IP address or hostname
export WYOMING_PORT=10300  # Default Wyoming port
```

## Debug Mode

Enable detailed logging for troubleshooting:

```bash
export DEBUG=true
```

Debug mode provides information about:
- Request details and headers
- Audio processing metrics
- Transcription timing and performance
- Service communication details

### Nix

If you are using Nix and have the Flakes experimental feature activated, you can start a development shell and test run the server with the following commands:

```
nix develop github:negatethis/rebble-asr
python3 -m gunicorn -k gevent 0.0.0.0:8080 asr:app
```

Make sure to export the appropriate environment variables as described above.

There is also a NixOS module exposed through the flake.nix. Here is an example on how to add it to your system's flake.nix:

```nix
{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    rebble-asr.url = "github:negatethis/rebble-asr";
  };

  outputs = { self, nixpkgs, rebble-asr }: {
    nixosConfigurations.nixos = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        rebble-asr.nixosModules.default {
          services.rebble-asr = {
            enable = true;
            bind = "0.0.0.0:8080";
            environmentFile = "/path/to/environment/file"
          };
        }
      ];
    };
  };
}
```

The environment file should contain the environment variables described above.
