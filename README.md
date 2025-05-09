# ODNS Client README

## English

### ODNS Client

This document describes the principles, purpose, configuration, and limitations of the ODNS (Oblivious DNS) Client.

#### Principles of ODNS

The ODNS Client enhances user privacy and DNS integrity by employing a two-stage process for DNS resolution:

1.  **Query Encryption**: The client encrypts the DNS query content (e.g., the domain name you want to resolve) using **Elliptic Curve Integrated Encryption Scheme (ECIES)**. This is an asymmetric encryption method, meaning a public key published in Service Node's TXT record is used to encrypt the query, and a corresponding private key is required for decryption.
2.  **Query Forwarding as Subdomain**: The encrypted query is then encoded and formatted as a subdomain. This subdomain is appended to the domain of a designated ODNS service node (resolver). For example, if the encrypted query is `[encrypted_query_string]` and the service node's domain is `odns.example.com`, the query sent into the DNS system will look something like `[encrypted_query_string].odns.example.com`.
3.  **Recursive Resolver Obliviousness**: This complete query (e.g., `[encrypted_query_string].odns.example.com`) is sent to a standard recursive DNS server (which could be your ISP's resolver, a public resolver like Google's 8.8.8.8, or one you specify). Crucially, this recursive server cannot decrypt the `[encrypted_query_string]` part; it only sees an undecipherable string and the domain of the ODNS service node (`odns.example.com`). It then forwards the query to the authoritative nameservers for `odns.example.com`, which is the ODNS service node.
4.  **Service Node Processing and Privacy**: The ODNS service node receives the query. It holds the private key necessary to decrypt the `[encrypted_query_string]` part and retrieve the original DNS query (e.g., "what is the IP address for `www.myactualdomain.com`?"). The ODNS service node then resolves this original query (acting as a recursive resolver itself for this step). Importantly, the ODNS service node only sees the IP address of the *recursive server* that forwarded the request; it does **not** see the original user's IP address.
5.  **Encrypted Response**: The ODNS service node encrypts the response and sends it back through the recursive server to the client. The client then decrypts the response.

This mechanism ensures:
* The recursive DNS server (e.g., your ISP) does not know the actual domain name you are querying.
* The ODNS service node (which resolves the query) does not know your IP address.

#### Purpose of ODNS

* **Prevent Domain Spoofing and Hijacking**: By encrypting the query, ODNS helps prevent network operators or malicious actors from easily intercepting, reading, or modifying your DNS queries to redirect you to fake or malicious sites.
* **Enhance User Privacy**: ODNS ensures that neither the intermediate recursive resolver nor the final service node that resolves the query has the complete picture of who is asking for what domain. The recursive resolver doesn't know the content of the query, and the service node doesn't know the original user's IP address.

#### Configuration Method

The configuration in Coredns 设置 tab is as follows:

`vpn-send <node_domain> <recursive_server_ip>`

* `<node_domain>`: This is the domain name of the ODNS service node that will decrypt and resolve your queries. For example, `odns.example.com`.
* `<recursive_server_ip>`: This is the IP address of the recursive DNS server that the client will initially send its encrypted queries to. This server will then forward the query to the specified `<node_domain>`. For example, `8.8.8.8` or your preferred recursive resolver.


#### Current Limitations

* **A-Type Queries Only**: Currently, this ODNS client implementation only supports A-type DNS queries (which map a hostname to an IPv4 address).
* **Fallback for Other Query Types**: Any other type of DNS query (e.g., AAAA, MX, CNAME, TXT, etc.) will bypass the ODNS encryption mechanism and be forwarded directly to the recursive server specified in the `forward` configuration (or a default one if `forward` is not explicitly set for these types).
#### Licensing and Acknowledgements

This project utilizes CoreDNS. CoreDNS is licensed under the Apache License, Version 2.0.
You can obtain a copy at [http://www.apache.org/licenses/LICENSE-2.0](http://www.apache.org/licenses/LICENSE-2.0).

---

## 中文 (Chinese)

### ODNS 客户端 README

本文档描述了 ODNS (Oblivious DNS) 客户端的原理、作用、配置方法及目前的限制。

#### ODNS 原理

ODNS 客户端通过以下两阶段过程来增强用户隐私和 DNS 解析的完整性：

1.  **查询加密**：客户端使用 **椭圆曲线集成加密方案 (ECIES)** 来加密 DNS 查询内容（例如，您想要解析的域名）。这是一种非对称加密方法，意味着使用节点在TXT记录中公布的公钥加密查询，并需要相应的私钥进行解密。
2.  **作为子域名转发查询**：加密后的查询内容会被编码并格式化为一个子域名。这个子域名会被附加到指定的 ODNS 服务节点（解析器）的域名上。例如，如果加密后的查询是 `[encrypted_query_string]`，服务节点的域名是 `odns.example.com`，那么发送到 DNS 系统中的查询将类似于 `[encrypted_query_string].odns.example.com`。
3.  **递归服务器的无感知性**：这个完整的查询（例如 `[encrypted_query_string].odns.example.com`）被发送到一个标准的递归 DNS 服务器（可能是您的 ISP 的解析器、像谷歌的 8.8.8.8 这样的公共解析器，或您指定的解析器）。关键在于，这个递归服务器无法解密 `[encrypted_query_string]` 部分；它只能看到一个无法解读的字符串和 ODNS 服务节点的域名 (`odns.example.com`)。然后它会将查询转发到 `odns.example.com` 的权威域名服务器，即 ODNS 服务节点。
4.  **服务节点处理与隐私保护**：ODNS 服务节点接收到查询。它持有解密 `[encrypted_query_string]` 部分并检索原始 DNS 查询（例如，“`www.myactualdomain.com` 的 IP 地址是什么？”）所必需的私钥。然后，ODNS 服务节点解析这个原始查询（在此步骤中它本身充当递归解析器）。重要的是，ODNS 服务节点只能看到转发请求的*递归服务器*的 IP 地址；它**无法得知**原始用户的 IP 地址。
5.  **加密响应**：ODNS 服务节点加密响应，并通过递归服务器将其发送回客户端。客户端随后解密响应。

此机制确保：
* 递归 DNS 服务器（例如您的 ISP）不知道您实际查询的域名内容。
* ODNS 服务节点（负责解析查询）不知道您的 IP 地址。

#### ODNS 的作用

* **保证域名不被篡改和劫持**：通过加密查询，ODNS 有助于防止网络运营商或恶意行为者轻易截取、读取或修改您的 DNS 查询，从而将您重定向到虚假或恶意站点。
* **保证用户隐私**：ODNS 确保中间的递归解析器和最终解析查询的服务节点都不会掌握“谁在请求什么域名”的完整信息。递归解析器不知道查询的内容，服务节点不知道原始用户的 IP 地址。

#### 配置方法

Coredns 设置Tab中的核心配置如下：

`vpn-send <节点域名> <要使用的递归服务器IP>`

* `<节点域名>`：这是 ODNS 服务节点的域名，该节点将解密并解析您的查询。例如 `odns.example.com`。
* `<要使用的递归服务器IP>`：这是客户端最初将其加密查询发送到的递归 DNS 服务器的 IP 地址。该服务器随后会将查询转发到指定的 `<节点域名>`。例如 `8.8.8.8` 或您偏好的递归解析器。


#### 目前的限制

* **仅支持 A 类型查询**：目前，此 ODNS 客户端实现仅支持 A 类型 DNS 查询（将主机名映射到 IPv4 地址）。
* **其他类型查询的转发**：任何其他类型的 DNS 查询（例如 AAAA、MX、CNAME、TXT 等）将绕过 ODNS 加密机制，并直接转发到 `forward` 配置中指定的递归服务器。
#### 许可与致谢

本项目使用了 CoreDNS。CoreDNS 在 Apache License, Version 2.0 下获得许可。
您可以从 [http://www.apache.org/licenses/LICENSE-2.0](http://www.apache.org/licenses/LICENSE-2.0) 获取。
