# Souls Animation Studio

Extension Blender libre, indépendante de tout projet Godot, inspirée de Quick Rigging et d’AI Inbetweening dans Cascadeur. Le **Sprint 0** fournit un panneau et des diagnostics locaux. Le rigging, l’export et la génération d’animations ne sont pas encore implémentés. Le ZIP ne contient ni modèle ni dépendance IA et ne nécessite ni réseau, ni compte, ni abonnement.

## Installer

1. Récupérer [`dist/souls_animation_studio-0.1.0.zip`](dist/souls_animation_studio-0.1.0.zip) ou le générer ci-dessous si le dépôt vient d’être cloné.
2. Dans Blender, ouvrir **Édition → Préférences → Obtenir des extensions**, puis choisir **Installer depuis le disque** dans le menu et sélectionner le ZIP. Vérifier que **Souls Animation Studio** est activée dans **Add-ons**.
3. Dans la vue 3D, appuyer sur `N` et ouvrir **Souls Animation Studio**. Le panneau affiche les deux sections prévues et les diagnostics de l’instance Blender, dont le moteur graphique et le rendu GPU en mode interactif. Ces informations ne constituent pas une vérification de compatibilité IA.

Minimum déclaré : Blender **4.2.0**. Sprint 0 testé sous **5.2.2 LTS** uniquement.

## Développer et tester

Depuis la racine du dépôt, avec Bash, Blender et Python système 3.11+ :

```sh
bash scripts/build_extension.sh
bash scripts/test_headless.sh
```

`BLENDER_BIN=/chemin/vers/blender` permet de choisir un autre binaire. `TMPDIR` permet de choisir l’emplacement des profils temporaires. Le script de test valide le ZIP, l’installe hors ligne dans un profil jetable, puis relance Blender pour tester l’extension installée, pas le code source. Pour tester un ZIP spécifique : `bash scripts/test_headless.sh /chemin/vers/extension.zip`. Les ZIP de `dist/` ne sont pas suivis dans Git.

Les cinq agents OpenCode sont définis dans `.opencode/agents/`. Dans une **nouvelle session** ouverte depuis ce dépôt, `addon-director` est l’agent principal par défaut. Voir `opencode debug agents` pour contrôler les profils chargés. Les permissions s’appliquent au projet ; elles ne remplacent pas un bac à sable système (notamment lorsqu’on autorise une commande shell).

- [Architecture et frontières](docs/architecture.md)
- [Étude de Rigify, UniRig, CondMDI et MDM](docs/etude-backends.md)
- [Environnement, validation et plan du sprint suivant](docs/sprint-0.md)
- [Instructions pour les agents](AGENTS.md)

Licence : [GPL-3.0-or-later](LICENSE). Les futurs moteurs, poids et jeux de données conserveront leurs propres conditions.
