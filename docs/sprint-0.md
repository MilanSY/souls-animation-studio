# Sprint 0 — validation du 24 novembre 2026

## Environnement constaté

| Élément | Résultat |
| --- | --- |
| OS | Omarchy / Arch Linux x86_64, noyau `7.2.15-4-omarchy` |
| Blender | 5.2.2 LTS, build `d13f752e3b9c` |
| Python système / dans Blender | 3.14.8 / 3.14.8 |
| OpenCode | 2.4.1 ; `opencode debug config` et `opencode debug agents` ont détecté la configuration locale et les cinq profils (un principal, quatre spécialistes) |
| GPU | Intel HD Graphics 520 (Skylake, PCI `8086:1916`), pilote `i915`, nœud de rendu DRM présent |
| Pile graphique | Mesa et `vulkan-intel` `1:26.2.5-1` installés ; Vulkan et le calcul Intel n’ont pas été testés |
| CUDA | Aucune carte NVIDIA détectée par `lspci`, pas de `nvidia-smi` ; cette machine ne remplit pas les prérequis CUDA annoncés pour UniRig |

La configuration OpenCode globale a été consultée en lecture seule : les empreintes du fichier serveur et de `cli.json` sont identiques avant et après. Le projet ne redéfinit pas de modèle ni de fournisseur. Les règles de délégation effectives ont été vérifiées dans la sortie CLI, sans exécuter les agents.

## Tests et limites

- `bash scripts/build_extension.sh` : manifeste source validé et ZIP construit via la CLI de Blender.
- `bash scripts/test_headless.sh` : ZIP validé et installé/activé hors ligne ; **4 tests réussis** après redémarrage avec le profil de test. Ils couvrent le contenu de l’archive, les préférences du module installé, l’enregistrement et le contenu du panneau, puis la désactivation/réactivation.
- Premier passage : **3/4**, lecture de `bl_idname` sur l’instance RNA non fiable. Le test vérifie maintenant le type de l’instance enregistrée et l’identifiant déclaré sur la classe.
- Essai Rigify séparé, en profil vierge : activation de la version 0.6.10, création du métarig humain (159 os) et génération du rig (706 os, dont 160 de déformation). Ni maillage ni skinning dans cet essai.
- Le test headless exécute la méthode `draw` avec un enregistreur de libellés ; il ne valide ni le rendu du panneau ni le chemin de diagnostic GPU interactif. Blender 4.2, l’intégration Godot et l’inférence IA n’ont pas été testés.

## Sprint 1 proposé

1. Créer un humanoïde de test sous une licence compatible. Placer manuellement les repères, ajuster un métarig Rigify, évaluer le skinning automatique et générer les contrôleurs IK/FK. Mesurer couverture et normalisation des poids et vérifier le résultat sur quelques poses.
2. Construire une référence CPU non-IA sur plusieurs poses clés ; écrire dans une nouvelle action, vérifier l’identité des clés originales et mesurer le glissement des pieds et l’écart main-accessoire sur des contacts annotés.
3. Tester l’export GLB/glTF du squelette de déformation et d’un clip cuit dans un projet Godot jetable. Valider aussi l’installation et l’affichage sous Blender 4.2 LTS et 5.2 LTS.
4. Spécifier et tester le contrat des adaptateurs ; séparer un futur essai UniRig sur CUDA d’un essai CondMDI ou MDM, conditionné à la validation des poids, des droits et de l’environnement.
