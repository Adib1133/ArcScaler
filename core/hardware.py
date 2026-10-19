"""
Hardware and GPU detection engine with specific Intel Arc architecture awareness.
Queries Windows display devices, CPU, and memory to provide hardware-tailored
OptiScaler XeSS and Frame Generation configurations.
"""

import os
import platform
import psutil
from typing import Dict, List, Optional, Any


class HardwareDetector:
    """Detects system hardware with dedicated Intel Arc classification."""

    ARC_IDENTIFIERS = (
        'arc(tm)', 'arc(r)', 'arc a', 'arc b', 'arc pro',
        'intel arc', 'arc graphics', 'arc 1', 'arc 2'
    )

    @staticmethod
    def _read_registry_adapters() -> List[Dict[str, str]]:
        adapters = []
        if os.name != 'nt':
            return adapters
        try:
            import winreg
            key_path = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as base:
                index = 0
                while True:
                    try:
                        sub = winreg.EnumKey(base, index)
                        index += 1
                        if not sub.isdigit():
                            continue
                        with winreg.OpenKey(base, sub) as k:
                            try:
                                name, _ = winreg.QueryValueEx(k, 'DriverDesc')
                            except OSError:
                                try:
                                    name, _ = winreg.QueryValueEx(k, 'Device Description')
                                except OSError:
                                    continue
                            driver_ver = ''
                            try:
                                driver_ver, _ = winreg.QueryValueEx(k, 'DriverVersion')
                            except OSError:
                                pass
                            provider = ''
                            try:
                                provider, _ = winreg.QueryValueEx(k, 'ProviderName')
                            except OSError:
                                pass
                            adapters.append({
                                'name': str(name).strip(),
                                'driver_version': str(driver_ver).strip(),
                                'provider': str(provider).strip()
                            })
                    except OSError:
                        break
        except Exception:
            pass
        return adapters

    @staticmethod
    def _classify_gpu(name: str, provider: str = '') -> Dict[str, Any]:
        lower_name = name.lower()
        lower_prov = provider.lower()

        is_intel = 'intel' in lower_name or 'intel' in lower_prov
        is_nvidia = 'nvidia' in lower_name or 'geforce' in lower_name or 'quadro' in lower_name or 'rtx' in lower_name or 'gtx' in lower_name
        is_amd = 'amd' in lower_name or 'radeon' in lower_name or 'advanced micro devices' in lower_prov

        is_arc = False
        xmx_supported = False

        if is_intel:
            for ident in HardwareDetector.ARC_IDENTIFIERS:
                if ident in lower_name:
                    is_arc = True
                    break
            if not is_arc and ('core ultra' in lower_name or 'intel(r) arc' in lower_name):
                is_arc = True

            # Discrete Arc cards (A-series, B-series, Pro) feature dedicated XMX hardware engines
            xmx_models = ('a770', 'a750', 'a580', 'a380', 'a310', 'a770m', 'a730m', 'a570m', 'a550m', 'a530m', 'a370m', 'a350m', 'b580', 'b570', 'b560', 'b550', 'pro a')
            if is_arc and any(m in lower_name for m in xmx_models):
                xmx_supported = True

        if is_intel:
            vendor = 'Intel'
        elif is_nvidia:
            vendor = 'NVIDIA'
        elif is_amd:
            vendor = 'AMD'
        else:
            vendor = 'Generic'

        return {
            'name': name,
            'vendor': vendor,
            'is_arc': is_arc,
            'has_xmx': xmx_supported,
            'architecture': 'Intel Xe-HPG/Battlemage (Arc)' if is_arc else f'{vendor} GPU',
            'xess_acceleration': 'XMX AI Hardware Cores' if xmx_supported else ('DP4a Instructions' if (is_intel or is_amd) else 'NVIDIA Tensor / DP4a')
        }

    @staticmethod
    def get_system_info() -> Dict[str, Any]:
        """Returns comprehensive hardware diagnostics and tailored recommendations."""
        gpus = []
        raw_adapters = HardwareDetector._read_registry_adapters()

        for a in raw_adapters:
            classified = HardwareDetector._classify_gpu(a['name'], a.get('provider', ''))
            classified['driver_version'] = a.get('driver_version', 'Unknown')
            # Avoid duplicate entries
            if not any(g['name'] == classified['name'] for g in gpus):
                gpus.append(classified)

        # Fallback if registry query returned nothing
        if not gpus:
            gpus.append({
                'name': 'Standard Display Adapter',
                'vendor': 'Unknown',
                'is_arc': False,
                'has_xmx': False,
                'architecture': 'Unknown',
                'xess_acceleration': 'DP4a Fallback',
                'driver_version': 'Unknown'
            })

        # Primary GPU selection priority: Intel Arc first, then discrete (NVIDIA/AMD), then other Intel
        def gpu_sort_key(g):
            if g['is_arc'] and g['has_xmx']:
                return 3
            if g['is_arc']:
                return 2
            if g['vendor'] in ('NVIDIA', 'AMD'):
                return 1
            return 0

        gpus.sort(key=gpu_sort_key, reverse=True)
        primary_gpu = gpus[0]

        # CPU detection
        cpu_name = platform.processor() or 'x86_64 Processor'
        if os.name == 'nt':
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as k:
                    name, _ = winreg.QueryValueEx(k, 'ProcessorNameString')
                    cpu_name = str(name).strip()
            except Exception:
                pass

        # Memory in GB
        try:
            total_ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        except Exception:
            total_ram_gb = 0.0

        # OS information
        os_info = f'{platform.system()} {platform.release()} ({platform.machine()})'

        # Generate hardware-tailored OptiScaler recommendations
        is_arc = primary_gpu['is_arc']
        is_intel = primary_gpu['vendor'] == 'Intel'

        recommendations = {
            'is_arc': is_arc,
            'has_xmx': primary_gpu['has_xmx'],
            # Intel / Arc GPUs must keep DXGI GPU spoofing off to prevent ray-tracing startup crashes
            'gpu_spoofing': False,
            'spoofing_warning': 'Disabled for Intel Arc to prevent DXGI ray-tracing startup crashes.' if is_intel else 'Optional for non-Intel GPUs.',
            # Auto allows OptiScaler / XeSS to select native XMX on Arc and DP4a on others
            'xess_network_model': 'auto',
            'reflex_to_xell': is_arc or is_intel,
            'reflex_backend_note': 'Intel XeLL (Xe Low Latency) active via FakeNvapi.' if (is_arc or is_intel) else 'Standard latency pacing.',
            'xess_backend': 'Intel XMX Hardware' if primary_gpu['has_xmx'] else 'DP4a Compatible Instruction Set'
        }

        badge_text = f"Arc: {primary_gpu['name']}" if is_arc else f"GPU: {primary_gpu['name']}"
        if is_arc and primary_gpu['has_xmx']:
            badge_text += ' (XMX)'
        elif not is_arc and is_intel:
            badge_text += ' (DP4a)'

        return {
            'primary_gpu': primary_gpu,
            'all_gpus': gpus,
            'is_arc_detected': is_arc,
            'cpu': cpu_name,
            'ram_gb': total_ram_gb,
            'os': os_info,
            'recommendations': recommendations,
            'badge_text': badge_text
        }
