import subprocess
import platform
import logging
import os
import shutil # For file backup on Linux

logger = logging.getLogger(__name__)

DNS_TIMEOUT = 15  # Seconds for DNS command timeout

class DNSManager:
    """
    Manages DNS settings for different operating systems.
    """
    def __init__(self):
        self.os_type = platform.system().lower()
        self.original_dns_servers = {} # For macOS: {service: [dns1, dns2]}
                                       # For Windows: {"main": [dns1, dns2]} (simplified)
                                       # For Linux: {"resolv_backup_path": "/path/to/backup"}
        self.resolv_conf_path = "/etc/resolv.conf"
        self.resolv_backup_path = f"{self.resolv_conf_path}.corednscontroller.backup"


    def _run_command(self, command_parts, timeout=DNS_TIMEOUT):
        """
        Runs a command using subprocess and returns its output.
        Args:
            command_parts (list): The command and its arguments.
            timeout (int): Timeout in seconds.
        Returns:
            tuple: (stdout, stderr, returncode)
        """
        try:
            logger.debug(f"Executing command: {' '.join(command_parts)}")
            
            # --- Modification to hide PowerShell window on Windows ---
            creation_flags = 0
            if self.os_type == "windows" and command_parts[0].lower() == "powershell":
                # 0x08000000 is subprocess.CREATE_NO_WINDOW
                # This flag prevents the console window from appearing.
                creation_flags = 0x08000000 
            
            process = subprocess.Popen(
                command_parts, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                text=True,
                creationflags=creation_flags # Apply the flag here
            )
            stdout, stderr = process.communicate(timeout=timeout)
            logger.debug(f"Command stdout: {stdout.strip()}")
            if stderr.strip():
                logger.debug(f"Command stderr: {stderr.strip()}")
            return stdout.strip(), stderr.strip(), process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            logger.error(f"Command timed out: {' '.join(command_parts)}")
            return stdout.strip(), f"Command timed out after {timeout} seconds.\n{stderr.strip()}", -1 # Indicate timeout
        except FileNotFoundError:
            logger.error(f"Command not found: {command_parts[0]}")
            return "", f"Command not found: {command_parts[0]}", -2 # Indicate file not found
        except Exception as e:
            logger.error(f"Error executing command {' '.join(command_parts)}: {e}")
            return "", str(e), -3 # Indicate other error

    def is_privileged(self):
        """Checks if the current user has administrative privileges."""
        if self.os_type == "windows":
            try:
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception as e:
                logger.error(f"Error checking admin status on Windows: {e}")
                return False
        elif self.os_type in ["linux", "darwin"]: # macOS is 'darwin'
            return os.geteuid() == 0
        return False

    def get_original_dns(self):
        """Gets and stores the original DNS settings."""
        logger.info("Getting original DNS settings...")
        self.original_dns_servers = {} # Reset
        if self.os_type == "windows":
            ps_command = """
            $ErrorActionPreference = 'Stop'
            try {
                $adapters = Get-NetAdapter | Where-Object {$_.Status -eq 'Up' -and $_.MediaType -ne 'Loopback' -and $_.Virtual -eq $false}
                $allDns = @()
                foreach ($adapter in $adapters) {
                    $dnsSettings = Get-DnsClientServerAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
                    if ($dnsSettings -and $dnsSettings.ServerAddresses) {
                        $allDns += $dnsSettings.ServerAddresses
                    }
                }
                $uniqueDns = $allDns | Where-Object {$_ -ne $null -and $_.Trim() -ne '' -and $_ -ne '127.0.0.1' -and $_ -notmatch '^::1'} | Select-Object -Unique
                if ($uniqueDns.Count -gt 0) {
                    Write-Output ($uniqueDns -join ',')
                } else { Write-Output "" }
            } catch { Write-Error "Failed to get DNS: $($_.Exception.Message)"; exit 1 }
            """
            stdout, stderr, rc = self._run_command(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command])
            if rc == 0 and stdout:
                self.original_dns_servers["main"] = [s.strip() for s in stdout.split(',') if s.strip()]
                logger.info(f"Original Windows DNS: {self.original_dns_servers['main']}")
            elif rc != 0:
                logger.error(f"Failed to get original Windows DNS. RC: {rc}, Stderr: {stderr}")
                return False, f"获取原始DNS失败: {stderr}"
            else:
                logger.info("No specific original DNS found on Windows (likely DHCP).")
                self.original_dns_servers["main"] = [] # Mark as DHCP
            return True, ""

        elif self.os_type == "darwin": # macOS
            stdout, stderr, rc = self._run_command(["networksetup", "-listallnetworkservices"])
            if rc != 0:
                logger.error(f"Failed to list network services on macOS: {stderr}")
                return False, f"列出网络服务失败: {stderr}"
            
            services_to_check = []
            for line in stdout.splitlines():
                if line.startswith("*"): continue # Skip disabled
                if any(kw in line for kw in ["Wi-Fi", "Ethernet", "USB", "Thunderbolt"]):
                    info_stdout, _, _ = self._run_command(["networksetup", "-getinfo", line])
                    enabled_stdout, _, _ = self._run_command(["networksetup", "-getnetworkserviceenabled", line])
                    if "IP address:" in info_stdout and \
                       "IP address: <nil>" not in info_stdout and \
                       "IP address: (null)" not in info_stdout and \
                       "Status: Inactive" not in info_stdout and \
                       "Disabled" not in enabled_stdout:
                        services_to_check.append(line)

            if not services_to_check:
                logger.info("No active and relevant network services found on macOS.")
                return True, "" 

            for service in services_to_check:
                stdout_dns, stderr_dns, rc_dns = self._run_command(["networksetup", "-getdnsservers", service])
                if rc_dns == 0:
                    if "aren't any DNS Servers set" in stdout_dns or "aren't specified" in stdout_dns or not stdout_dns.strip():
                        self.original_dns_servers[service] = ["empty"] 
                        logger.info(f"Original DNS for service '{service}' (macOS): DHCP/empty")
                    else:
                        self.original_dns_servers[service] = [s.strip() for s in stdout_dns.splitlines() if s.strip()]
                        logger.info(f"Original DNS for service '{service}' (macOS): {self.original_dns_servers[service]}")
                else:
                    logger.warning(f"Could not get DNS for service '{service}' on macOS: {stderr_dns}")
            return True, ""

        elif self.os_type == "linux":
            if os.path.islink(self.resolv_conf_path):
                link_target = os.readlink(self.resolv_conf_path)
                if "systemd" in link_target or "NetworkManager" in link_target:
                    logger.warning(f"{self.resolv_conf_path} is a symlink to {link_target}. Direct modification might be overwritten.")
            try:
                with open(self.resolv_conf_path, 'r') as f_curr:
                    content = f_curr.read()
                    if "systemd-resolved" in content or "NetworkManager" in content:
                         logger.warning(f"{self.resolv_conf_path} content suggests it's managed by systemd-resolved or NetworkManager.")
            except Exception:
                pass 

            try:
                if os.path.exists(self.resolv_backup_path):
                    logger.info(f"Backup file {self.resolv_backup_path} already exists. Will overwrite for this session.")
                shutil.copy2(self.resolv_conf_path, self.resolv_backup_path)
                self.original_dns_servers["resolv_backup_path"] = self.resolv_backup_path
                logger.info(f"Backed up {self.resolv_conf_path} to {self.resolv_backup_path}")
                return True, ""
            except Exception as e:
                logger.error(f"Failed to backup {self.resolv_conf_path}: {e}")
                return False, f"备份 {self.resolv_conf_path} 失败: {e}"
        return True, "" 

    def set_dns(self, dns_server="127.0.0.1"):
        """Sets the DNS server(s) to the specified IP."""
        logger.info(f"Setting DNS to {dns_server}...")
        if self.os_type == "windows":
            ps_command = f"""
            $ErrorActionPreference = 'Stop'
            $WarningPreference = 'SilentlyContinue' 
            $adapters = Get-NetAdapter | Where-Object {{($_.Status -eq 'Up' -and $_.MediaType -ne 'Loopback' -and $_.Virtual -eq $false)}}
            $successCount = 0
            if ($adapters.Count -eq 0) {{ Write-Warning "No active adapters found to set DNS."; exit 0 }}
            foreach ($adapter in $adapters) {{
                try {{
                    Set-DnsClientServerAddress -InterfaceIndex $adapter.InterfaceIndex -ServerAddresses "{dns_server}" -ErrorAction Stop
                    Write-Host "Set DNS for $($adapter.Name) (Index $($adapter.InterfaceIndex))"
                    $successCount++
                }} catch {{
                    Write-Warning "Failed to set DNS for $($adapter.Name) (Index $($adapter.InterfaceIndex)): $($_.Exception.Message)"
                }}
            }}
            if ($successCount -gt 0) {{ exit 0 }} else {{ Write-Error "Failed to set DNS on any active adapter."; exit 1 }}
            """
            _, stderr, rc = self._run_command(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command])
            if rc != 0:
                logger.error(f"Failed to set Windows DNS. RC: {rc}, Stderr: {stderr}")
                return False, f"设置DNS失败: {stderr}"
            logger.info("Windows DNS set successfully (attempted on all active adapters).")
            return True, ""

        elif self.os_type == "darwin":
            if not self.original_dns_servers: 
                logger.warning("Original DNS not fetched for macOS, cannot determine services to set DNS for.")
                success, msg = self.get_original_dns()
                if not success or not self.original_dns_servers:
                     return False, f"无法确定网络服务以设置DNS: {msg}"

            success_count = 0
            last_err = ""
            for service in self.original_dns_servers.keys(): 
                _, stderr, rc = self._run_command(["networksetup", "-setdnsservers", service, dns_server])
                if rc != 0:
                    logger.error(f"Failed to set DNS for service '{service}' on macOS: {stderr}")
                    last_err = stderr
                else:
                    logger.info(f"DNS set for service '{service}' on macOS.")
                    success_count += 1
            if success_count == 0 and self.original_dns_servers:
                return False, f"设置DNS失败 (macOS): {last_err}"
            elif not self.original_dns_servers:
                 return False, "没有找到活动的网络服务来设置DNS (macOS)."
            return True, ""

        elif self.os_type == "linux":
            try:
                content = f"nameserver {dns_server}\n# Modified by CoreDNSController\n"
                if "resolv_backup_path" in self.original_dns_servers and \
                   os.path.exists(self.original_dns_servers["resolv_backup_path"]):
                    with open(self.original_dns_servers["resolv_backup_path"], 'r') as bak_f:
                        for line in bak_f:
                            trimmed_line = line.strip()
                            if trimmed_line and not trimmed_line.startswith("nameserver") and not trimmed_line.startswith("#"):
                                content += line
                
                with open(self.resolv_conf_path, 'w') as f:
                    f.write(content)
                logger.info(f"DNS set in {self.resolv_conf_path}")
                return True, ""
            except Exception as e:
                logger.error(f"Failed to write to {self.resolv_conf_path}: {e}")
                return False, f"写入 {self.resolv_conf_path} 失败: {e}"
        return False, "不支持的操作系统"


    def restore_original_dns(self):
        """Restores the original DNS settings."""
        logger.info("Restoring original DNS settings...")
        if self.os_type == "windows":
            dns_to_restore = self.original_dns_servers.get("main", [])
            ps_command = ""
            if dns_to_restore: 
                servers_str = ",".join([f'"{s}"' for s in dns_to_restore])
                ps_command = f"""
                $ErrorActionPreference = 'Stop'; $WarningPreference = 'SilentlyContinue';
                $adapters = Get-NetAdapter | Where-Object {{($_.Status -eq 'Up' -and $_.MediaType -ne 'Loopback' -and $_.Virtual -eq $false)}};
                $successCount = 0;
                if ($adapters.Count -eq 0) {{ Write-Warning "No active adapters to restore DNS."; exit 0 }};
                foreach ($adapter in $adapters) {{
                    try {{
                        Set-DnsClientServerAddress -InterfaceIndex $adapter.InterfaceIndex -ServerAddresses @({servers_str}) -ErrorAction Stop;
                        Write-Host "Restored DNS for $($adapter.Name)"; $successCount++;
                    }} catch {{ Write-Warning "Failed to restore DNS for $($adapter.Name): $($_.Exception.Message)" }}
                }};
                if ($successCount -gt 0) {{ exit 0 }} else {{ Write-Error "Failed to restore DNS on any adapter."; exit 1 }}
                """
                logger.info(f"Attempting to restore Windows DNS to: {dns_to_restore}")
            else: 
                ps_command = """
                $ErrorActionPreference = 'Stop'; $WarningPreference = 'SilentlyContinue';
                $adapters = Get-NetAdapter | Where-Object {{($_.Status -eq 'Up' -and $_.MediaType -ne 'Loopback' -and $_.Virtual -eq $false)}};
                $successCount = 0;
                if ($adapters.Count -eq 0) {{ Write-Warning "No active adapters to reset DNS."; exit 0 }};
                foreach ($adapter in $adapters) {{
                    try {{
                        Set-DnsClientServerAddress -InterfaceIndex $adapter.InterfaceIndex -ResetServerAddresses -ErrorAction Stop;
                        Write-Host "Reset DNS for $($adapter.Name)"; $successCount++;
                    }} catch {{ Write-Warning "Failed to reset DNS for $($adapter.Name): $($_.Exception.Message)" }}
                }};
                if ($successCount -gt 0) {{ exit 0 }} else {{ Write-Error "Failed to reset DNS on any adapter."; exit 1 }}
                """
                logger.info("Attempting to reset Windows DNS to DHCP.")

            _, stderr, rc = self._run_command(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_command])
            if rc != 0:
                logger.error(f"Failed to restore Windows DNS. RC: {rc}, Stderr: {stderr}")
                return False, f"恢复DNS失败: {stderr}"
            logger.info("Windows DNS restored/reset successfully.")
            return True, ""

        elif self.os_type == "darwin":
            if not self.original_dns_servers:
                logger.warning("No original DNS settings stored for macOS to restore.")
                success, msg = self.get_original_dns() 
                if not success or not self.original_dns_servers:
                    return False, f"无法确定网络服务以恢复DNS: {msg}"


            success_count = 0
            last_err = ""
            for service, dns_list in self.original_dns_servers.items():
                args = ["networksetup", "-setdnsservers", service]
                if "empty" in dns_list or not dns_list:
                    args.append("empty")
                    logger.info(f"Resetting DNS for service '{service}' on macOS to DHCP.")
                else:
                    args.extend(dns_list)
                    logger.info(f"Restoring DNS for service '{service}' on macOS to: {dns_list}")
                
                _, stderr, rc = self._run_command(args)
                if rc != 0:
                    logger.error(f"Failed to restore DNS for service '{service}' on macOS: {stderr}")
                    last_err = stderr
                else:
                    success_count +=1
            
            if success_count == 0 and self.original_dns_servers:
                return False, f"恢复DNS失败 (macOS): {last_err}"
            elif not self.original_dns_servers:
                logger.info("没有原始DNS服务信息可恢复 (macOS).")
            return True, ""

        elif self.os_type == "linux":
            backup_path = self.original_dns_servers.get("resolv_backup_path")
            if not backup_path or not os.path.exists(backup_path):
                logger.error(f"Backup file {backup_path or self.resolv_backup_path} not found. Cannot restore.")
                return False, f"备份文件 {backup_path or self.resolv_backup_path} 未找到，无法恢复。"
            try:
                shutil.copy2(backup_path, self.resolv_conf_path)
                logger.info(f"Restored {self.resolv_conf_path} from {backup_path}")
                try:
                    os.remove(backup_path)
                    logger.info(f"Removed backup file {backup_path}")
                except Exception as e_rem:
                    logger.warning(f"Could not remove backup file {backup_path}: {e_rem}")
                return True, ""
            except Exception as e:
                logger.error(f"Failed to restore {self.resolv_conf_path} from backup: {e}")
                return False, f"从备份恢复 {self.resolv_conf_path} 失败: {e}"
        return False, "不支持的操作系统"

