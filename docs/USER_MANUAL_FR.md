# QCALVIEW — Manuel utilisateur


## Prérequis de rendu

QCALVIEW nécessite un **MNT/MNS raster explicitement sélectionné** dans **Relief > Raster MNT/MNS** avant tout aperçu ou export visuel. La présence d’un raster dans le projet QGIS ne suffit pas : il doit être choisi comme topographie dans QCALVIEW.

## À propos de QCALVIEW
QCALVIEW est développé par **Fabrice Kerzerho — ArcTan°**, activité française spécialisée en simulation visuelle et géomatique : accompagnement des études paysagères, projets d’énergies renouvelables, simulations photographiques et workflows SIG.

- QCALVIEW : https://qcalview.com
- ArcTan° : https://arctan.fr
- Contact : contact@arctan.fr

## Objet
QCALVIEW intègre directement dans QGIS des fonctions de calage photographique et de simulation visuelle. Le plugin permet notamment d’associer des photographies à des points de vue, de projeter des couches SIG dans des vues calibrées, de produire des représentations schématiques, d’aider aux analyses d’occultation et d’exporter les vues préparées.

## Démarrage rapide
1. Charger ou sélectionner une couche de points représentant les points de vue.
2. Ajouter ou choisir un point de vue et lui associer une photographie si nécessaire.
3. Régler projection et paramètres caméra (azimut, tangage, roulis, HFOV/VFOV, hauteur caméra).
4. Ajouter les couches vectorielles compatibles dans la liste des couches projetées.
5. Régler leur apparence ou leur représentation schématique.
6. Contrôler l’aperçu/visionneuse puis enregistrer les paramètres et l’état visuel du PDV.
7. Utiliser l’onglet Export pour produire les sorties voulues.

## Important
Ce manuel reste volontairement concis pour QCALVIEW 40.21. Les procédures détaillées, captures et recommandations méthodologiques continueront à être enrichies.

## Outils expérimentaux dans QCALVIEW 40.21

La version 40.21 est publiée sur le canal QGIS standard. Certains outils avancés restent toutefois explicitement marqués comme expérimentaux, notamment la Grille de projection, la Règle azimutale et le Monoplotting interactif. Dans **Interroger le terrain depuis l'image**, la visionneuse affiche désormais une loupe automatique à **100 % des pixels natifs** de la photo source, y compris lorsque l'affichage courant repose sur un proxy réduit ; le réticule central indique le pixel X/Y, l'azimut et l'élévation, puis la distance après une intersection terrain réussie. Ces outils restent en cours de développement et leur comportement ou leur interface peuvent évoluer. Les autres modules internes en développement ne sont volontairement pas encore exposés dans les versions publiques.
