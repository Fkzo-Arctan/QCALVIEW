# QCALVIEW 40.22 — Notes de publication pour le dépôt QGIS

**Base :** QCALVIEW 40.22  
**Référence de comparaison :** 40.21 (CHANGELOG de la branche 40.22).  
**Nature :** mise à jour fonctionnelle et corrective.

## Nouveautés et améliorations depuis 40.21

### Visionneuses et rendu Multi-View

- Jusqu'à **quatre points de vue** dans des visionneuses simultanées, en fenêtres indépendantes ou dans une vue quadrillée (**Quad View**).
- **États temporaires de caméra indépendants** dans chaque visionneuse, y compris lors de l'ouverture de plusieurs versions d'un même point de vue.
- Synchronisation de la visionneuse active avec les paramètres du cockpit QCALVIEW.
- Ordonnancement **séquentiel** des rendus, caches partagés et bornés de photographies et de géométries pour limiter les recalculs et la consommation mémoire.
- Actualisation de la visionneuse active pour les modifications de caméra ; propagation des changements communs aux autres visionneuses, avec rendu léger puis affinement.
- Commande **Enregistrer l'état du point de vue** propre à chaque visionneuse ; persistance de la session Multi-View et des paramètres dans le projet QGIS.
- Correction du dessin des parois extrudées 2,5D sur les panoramas à la couture ±180° ; limitation des rendus pleine résolution redondants.

### Relief et LiDAR

- Sélecteur MNT/MNS acceptant **tout raster numérique monobande géoréférencé valide**, même à altitude constante ou sans mot-clé altimétrique dans son nom.
- Pas de calcul autorisé dès **0,5 m** ; pas proposé par défaut : **25 m** (valeurs de projet déjà enregistrées conservées).
- Maintien des sécurités adaptatives contre les calculs trop coûteux sur de grandes scènes.

### Altitude absolue et référentiel vertical — nouveauté 40.22

- Case **« Alt. absolue »** accolée à **« Alt./Z »**.
- Mode historique par défaut : altitude du terrain + hauteur de caméra ; mode absolu : la cote saisie est utilisée directement.
- Enregistrement du mode pour chaque point de vue dans le champ `qcv_altabs` (0/1).
- Paramètre **Référence altimétrique** : reprise du SCR vertical du projet QGIS (option par défaut) ou sélection d'un SCR vertical EPSG spécifique dans les options QCALVIEW, enregistrée dans le projet.
- Contrôle de la présence d'un SCR vertical ; comparaison caméra/MNT **ΔZ** lorsque la provenance des altitudes le permet. La différence reste annoncée comme indicative si le SCR vertical du raster est inconnu ; elle est désactivée en cas de référentiels différents.
- Aucune conversion de référentiel verticale ou correction barométrique automatique ; les valeurs sources ne sont pas modifiées.
- Propagation du mode dans les rendus, le monoplotting, la vue schématique, la sauvegarde par PDV et les exportations de métadonnées (sans renseigner arbitrairement `EXIF GPSAltitude` en mode absolu).

### Traductions

- **Suppression de l'installation d'un traducteur Qt global** par QCALVIEW. Celui-ci pouvait interférer avec les traductions françaises de menus natifs QGIS, notamment le menu contextuel des couches.
- Traductions FR/EN limitées aux commandes QCALVIEW et aux éléments de ses fenêtres ; textes statiques du plugin contrôlés face aux catalogues TS.
- Action de menu « Ajouter à QCALVIEW » conservée dans le menu des couches vectorielles.

## Compatibilité et périmètre

- QGIS 3.44 / Qt5 et QGIS 4.x / Qt6 visés.
- Conservés : fonctions et architecture **40.22** et ses outils expérimentaux historiques.
- **Non intégrés** : GCP 2.0, mode B/C automatique, Depth Maps, exports PLY/GLB et numérisation photo → carte de la branche 40.24/40.25.

## Contrôles réalisés

- Compilation syntaxique de **43 fichiers Python** : OK.
- Vérification XML des catalogues de traduction FR/EN : OK.
- Tests Python isolés : altitude relative et absolue (avec MNT), valeurs nulles, contrôle de référence verticale projet/spécifique, différence entre références, libellés du menu contextuel et absence de traducteur Qt global : OK.
- Existence et format des champs de configuration, du module Multi-View et des métadonnées du plugin : OK.
- Intégrité de l'archive ZIP finale : OK.
