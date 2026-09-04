# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 Fabrice Kerzerho — ArcTan°
# SPDX-License-Identifier: GPL-3.0-or-later
def classFactory(iface):
    from .qcalview_photo_plugin import QCalViewPlugin
    return QCalViewPlugin(iface)
