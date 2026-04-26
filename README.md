# Better Career + CareerMP Compatibility

Pacote de compatibilidade para usar Better Career como autoridade do modo carreira e CareerMP como ponte multiplayer no BeamMP.

## Artefatos

Os arquivos prontos ficam em `dist/`:

- `CareerMP_BetterCareer.zip`: cliente combinado para instalar como `Resources/Client/CareerMP.zip`.
- `CareerMP_BetterCareer_Server.zip`: recurso de servidor CareerMP com auto-update desativado.
- `build_report.json`: relatório do build, validação e boot-check.
- `checksums.sha256`: hashes SHA256 dos artefatos.

## O Que Foi Adaptado

- CareerMP original não substitui mais `career_career` por `career_careerMP`.
- Better Career continua controlando save, tutorial, garagem, marketplace, pintura, loans e spawn.
- CareerMP mantém eventos de sync, pagamento, UI apps, prefab sync, drag display sync e paint sync.
- O boot do CareerMP espera os módulos principais do Better Career antes de iniciar o save.
- O walking/unicycle do BeamMP foi compatibilizado com o spawn do Better Career.
- Travel nodes não pausam o jogador no BeamMP e viagem com destino único executa direto.
- Tráfego do Better Career respeita as flags do servidor CareerMP.

## Build

Pré-requisitos locais esperados pelo script:

- `C:\Users\bruni\Documents\PROJETOS\Jogos\BEANG\mods\better_career\better_career_mod_v0.5.0.zip`
- `C:\Users\bruni\Documents\PROJETOS\Jogos\BEANG\mods\better_career\CareerMP_v0.0.31.zip`

O script baixa o cliente oficial do CareerMP a partir da URL upstream usada no código.

```powershell
python .\scripts\build_better_career_careermp.py --boot-check --boot-timeout 45
```

## Servidor De Teste

O build instala um servidor limpo em:

```text
C:\Users\bruni\Documents\PROJETOS\Jogos\BEANG\servers\tests\better-career-careermp-west-coast
```

Porta usada no teste: `30831`.

## Resultado Validado

Último build funcional validado em BeamMP:

- Client hash: `5e136ad7c007ba924c034bf143296e7ccd87c50a1e3fdd836e98d78b24bb60fd`
- `zipfile.testzip()`: OK
- servidor carregou CareerMP
- auto-update desativado
- Better Career carregando corretamente com BeamMP + CareerMP

