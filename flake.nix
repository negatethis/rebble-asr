{
  description = "Flake for Rebble's ASR service";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    (flake-utils.lib.eachDefaultSystem (system:
      let pkgs = import nixpkgs { inherit system; }; in {

        packages = rec {
          rebble-speex = pkgs.python3Packages.buildPythonPackage rec {
            pname = "speex";
            version = "master";

            src = pkgs.fetchFromGitHub {
              repo = "pyspeex";
              owner = "jplexer";
              rev = "${version}";
              hash = "sha256-AxkqtdU2vwDgzALZprqUZwSuzP7Lb0S39VoAd8Wjepw=";
            };

            nativeBuildInputs = with pkgs; [
              gnumake
              python3Packages.cython
              python3Packages.setuptools
            ];

            buildInputs = with pkgs; [
              speex
            ];

            preBuild = ''
              make
            '';

            doCheck = false;
          };
          
          rebble-asr = pkgs.python3Packages.buildPythonPackage {
            pname = "rebble-asr";
            version = "master";

            src = ./.;

            format = "other";

            nativeBuildInputs = with pkgs; [
              speex
              speexdsp
            ];

            propagatedBuildInputs = with pkgs.python3Packages; [
              flask
              gevent
              gunicorn
              requests
              self.packages.${system}.rebble-speex
              wyoming
            ];

            installPhase = ''
              mkdir -p $out/lib/${pkgs.python3.libPrefix}/site-packages/rebble-asr
              cp -r asr/* $out/lib/${pkgs.python3.libPrefix}/site-packages/rebble-asr
            '';
          };

          default = rebble-asr;
        };

        devShells = rec {
          rebble-asr = pkgs.mkShell {
            packages = let
              python = pkgs.python3.withPackages(ps: with ps; [
                self.packages.${system}.rebble-asr
              ]);
            in [
              python
            ];
          };

          default = rebble-asr;
        };

        apps = rec {
          rebble-asr = flake-utils.lib.mkApp { drv = self.packages.${system}.rebble-asr; };
          default = rebble-asr;
        };

      })) //

    {
      nixosModules.default = { pkgs, lib, config, ... }:
        with lib;
        let
          cfg = config.services.rebble-asr;
        in
        {
          options.services.rebble-asr = {
            enable = mkEnableOption "Enable Rebble ASR service";
            bind = mkOption {
              type = types.str;
              default = "0.0.0.0:8080";
              example = "0.0.0.0:8080";
            };
            environmentFile = mkOption {
              type = types.path;
              default = null;
              example = "/opt/rebble-asr/.env";
            };
          };

          config = mkIf cfg.enable {
            systemd.services.rebble-asr = let
              python = pkgs.python3.withPackages (ps: with ps; [
                self.packages.${pkgs.system}.rebble-asr
              ]);
            in {
              description = "rebble-asr";
              wantedBy = [ "multi-user.target" ];
              wants = [ "network-online.target" ];
              after = [ "network-online.target" ];

              serviceConfig = {
                DynamicUser = true;
                ExecStart = "${python}/bin/python -m gunicorn -k gevent -b ${cfg.bind} asr:app";
                EnvironmentFile = "${cfg.environmentFile}";
              };
            };
          };
        };
    };
}

