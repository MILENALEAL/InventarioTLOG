import sqlite3
import streamlit as st
import pandas as pd
import requests
import urllib.parse
import re

GLPI_URL = "http://10.0.100.13/glpi/apirest.php"
USER_TOKEN = "Jxc68wvbHPyd9DGzBJ5VBwFHOlmQY9ZN7Npc8EE5"
APP_TOKEN = "kbHlm48ydwOcEnMtihIpWi4QyNcOIqIKWLfQrbQM"

st.set_page_config(page_title="Sistema de Gestão - TI", page_icon="💻", layout="wide")

if 'msg_lic' not in st.session_state: st.session_state.msg_lic = None

def inicializar_banco():
    conexao = sqlite3.connect('inventario_ti.db')
    cursor = conexao.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS itens (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, quantidade INTEGER NOT NULL, estoque_minimo INTEGER DEFAULT 2)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS licencas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, capacidade_maxima INTEGER DEFAULT 10)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS utilizadores (id INTEGER PRIMARY KEY AUTOINCREMENT, nome_pessoa TEXT NOT NULL, email_pessoa TEXT, id_licenca INTEGER, notebook TEXT, serial TEXT, id_computador TEXT, FOREIGN KEY (id_licenca) REFERENCES licencas (id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS fila_baixas (id INTEGER PRIMARY KEY AUTOINCREMENT, nome_item TEXT NOT NULL, quantidade INTEGER NOT NULL, centro_custo TEXT, pessoa_recebeu TEXT, concluido INTEGER DEFAULT 0)''')
    
    for col in [("notebook", "TEXT"), ("serial", "TEXT"), ("id_computador", "TEXT")]:
        try: cursor.execute(f"ALTER TABLE utilizadores ADD COLUMN {col[0]} {col[1]}")
        except: pass
    conexao.commit()
    return conexao

def safe_int(val):
    try: return int(val.from_bytes(val, 'little')) if isinstance(val, bytes) else int(val)
    except: return 0

def email_e_valido(email):
    return re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email) is not None

def conectar_glpi():
    headers = {"Content-Type": "application/json", "Authorization": f"user_token {USER_TOKEN}", "App-Token": APP_TOKEN}
    try:
        res = requests.get(f"{GLPI_URL}/initSession", headers=headers, timeout=5).json()
        headers["Session-Token"] = res['session_token']
        return headers
    except: return None

def consultar_glpi_completo(nome_pessoa):
    headers = conectar_glpi()
    if not headers: return "Erro", "-", None
    nome_f = urllib.parse.quote(nome_pessoa.replace(".", " "))
    try:
        url = f"{GLPI_URL}/search/Computer?criteria[0][field]=70&criteria[0][searchtype]=contains&criteria[0][value]={nome_f}&forcedisplay[0]=1&forcedisplay[1]=2&forcedisplay[2]=5"
        res = requests.get(url, headers=headers, timeout=5).json()
        if res.get('data') and len(res['data']) > 0:
            item = res['data'][0]
            return item.get('1', 'Desconhecido'), item.get('2', '-'), item.get('2')
        return "Não encontrado", "-", None
    except: return "Erro", "-", None
    finally:
        if headers and "Session-Token" in headers: requests.get(f"{GLPI_URL}/killSession", headers=headers)

def buscar_id_licenca_glpi(email_licenca):
    headers = conectar_glpi()
    if not headers: return None
    try:
        url = f"{GLPI_URL}/search/SoftwareLicense?criteria[0][field]=1&criteria[0][searchtype]=contains&criteria[0][value]={email_licenca}&forcedisplay[0]=2"
        res = requests.get(url, headers=headers, timeout=5).json()
        if res.get('data') and len(res['data']) > 0: return res['data'][0].get('2')
        return None
    except: return None
    finally:
        if headers and "Session-Token" in headers: requests.get(f"{GLPI_URL}/killSession", headers=headers)

def remover_vinculo_glpi(id_licenca, id_computador):
    headers = conectar_glpi()
    if not headers: return False
    try:
        url = f"{GLPI_URL}/Computer/{id_computador}/Item_SoftwareLicense"
        res = requests.get(url, headers=headers)
        if res.status_code in [200, 206]:
            for link in res.json():
                if str(link.get('softwarelicenses_id')) == str(id_licenca):
                    requests.delete(f"{GLPI_URL}/Item_SoftwareLicense/{link['id']}", headers=headers)
        return True 
    except: return False
    finally:
        if headers and "Session-Token" in headers: requests.get(f"{GLPI_URL}/killSession", headers=headers)

def vincular_no_glpi(id_licenca, id_computador):
    headers = conectar_glpi()
    if not headers: return False, "Falha conexão"
    try:
        payload = {"input": {"items_id": int(id_computador), "itemtype": "Computer", "softwarelicenses_id": int(id_licenca)}}
        res = requests.post(f"{GLPI_URL}/Item_SoftwareLicense", headers=headers, json=payload)
        return (True, "Sucesso") if res.status_code in [200, 201] else (False, res.text)
    except Exception as e: return False, str(e)
    finally:
        if headers and "Session-Token" in headers: requests.get(f"{GLPI_URL}/killSession", headers=headers)

def importar_e_sincronizar_tudo(caixa_log=None):
    conexao = sqlite3.connect('inventario_ti.db')
    cursor = conexao.cursor()
    headers = conectar_glpi()
    if not headers: return False, "Erro de conexão com o GLPI."

    try:
        cursor.execute("SELECT id, nome FROM licencas")
        licencas_locais = {row[1]: row[0] for row in cursor.fetchall()}
        
        if caixa_log: caixa_log.write("1/4: Puxando e-mails da equipe...")
        mapa_emails = {}
        s = 0
        while True:
            url_u = f"{GLPI_URL}/search/User?is_recursive=true&forcedisplay[0]=5&range={s}-{s+99}"
            res_u = requests.get(url_u, headers=headers)
            if res_u.status_code not in [200, 206]: break
            users = res_u.json()
            if not users or 'data' not in users or not isinstance(users['data'], list): break
            for u in users['data']:
                u_nome = u.get('1')
                u_email = u.get('5', '-')
                if u_nome:
                    if not u_email or str(u_email) in ['---', 'None', '']: u_email = '-'
                    mapa_emails[str(u_nome)] = str(u_email)
            if len(users['data']) < 100: break
            s += 100

        if caixa_log: caixa_log.write("2/4: Puxando inventário de máquinas...")
        comps_list = []
        s = 0
        while True:
            url_c = f"{GLPI_URL}/Computer?expand_dropdowns=true&is_recursive=true&range={s}-{s+99}"
            res_c = requests.get(url_c, headers=headers)
            if res_c.status_code not in [200, 206]: break
            comps = res_c.json()
            if not comps or not isinstance(comps, list): break
            for c in comps:
                comps_list.append({
                    'cid': c.get('id'),
                    'user': c.get('users_id'),
                    'note': c.get('name', '-'),
                    'ser': c.get('serial', '-')
                })
            if len(comps) < 100: break
            s += 100

        if caixa_log: caixa_log.write("3/4: Cruzando as suas licenças cadastradas...")
        mapa_comp_lic = {}
        for nome_lic_local, id_lic_local in licencas_locais.items():
            nome_f = urllib.parse.quote(nome_lic_local)
            url_search = f"{GLPI_URL}/search/SoftwareLicense?criteria[0][field]=1&criteria[0][searchtype]=contains&criteria[0][value]={nome_f}&is_recursive=true&forcedisplay[0]=2"
            res_s = requests.get(url_search, headers=headers)
            if res_s.status_code in [200, 206] and res_s.json().get('data'):
                id_lic_glpi = res_s.json()['data'][0].get('2')
                res_links = requests.get(f"{GLPI_URL}/SoftwareLicense/{id_lic_glpi}/Item_SoftwareLicense?is_recursive=true", headers=headers)
                if res_links.status_code in [200, 206] and isinstance(res_links.json(), list):
                    for link in res_links.json():
                        if str(link.get('itemtype')).lower() == 'computer':
                            mapa_comp_lic[link.get('items_id')] = id_lic_local

        if caixa_log: caixa_log.write("4/4: Salvando tudo no sistema...")
        inseridos, atualizados = 0, 0
        usuarios_com_note = set()

        for c in comps_list:
            c_id, u_nome, n_nome, s_num = c['cid'], c['user'], c['note'], c['ser']
            id_lic = mapa_comp_lic.get(c_id)

            if not u_nome or str(u_nome) in ['0', '', 'None', '---']:
                u_nome_final = f"Sem Usuário ({n_nome})"
                u_email = "-"
            else:
                u_nome_final = str(u_nome)
                u_email = mapa_emails.get(u_nome_final, '-')
                usuarios_com_note.add(u_nome_final)

            cursor.execute("SELECT id FROM utilizadores WHERE id_computador = ? OR (id_computador IS NULL AND nome_pessoa = ?)", (c_id, u_nome_final))
            reg = cursor.fetchone()
            if not reg:
                cursor.execute("INSERT INTO utilizadores (nome_pessoa, email_pessoa, id_licenca, notebook, serial, id_computador) VALUES (?, ?, ?, ?, ?, ?)", (u_nome_final, u_email, id_lic, n_nome, s_num, c_id))
                inseridos += 1
            else:
                cursor.execute("UPDATE utilizadores SET nome_pessoa=?, email_pessoa=?, id_licenca=?, notebook=?, serial=?, id_computador=? WHERE id=?", (u_nome_final, u_email, id_lic, n_nome, s_num, c_id, reg[0]))
                atualizados += 1

        for u_nome, u_email in mapa_emails.items():
            if u_nome not in usuarios_com_note:
                cursor.execute("SELECT id FROM utilizadores WHERE nome_pessoa = ?", (u_nome,))
                reg = cursor.fetchone()
                if not reg:
                    cursor.execute("INSERT INTO utilizadores (nome_pessoa, email_pessoa, id_licenca, notebook, serial, id_computador) VALUES (?, ?, NULL, '-', '-', NULL)", (u_nome, u_email))
                    inseridos += 1
                else:
                    cursor.execute("UPDATE utilizadores SET email_pessoa=? WHERE id=?", (u_email, reg[0]))
                    atualizados += 1

        conexao.commit()
        return True, f"Importação Jato Concluída! {inseridos} registros novos e {atualizados} atualizados."
    except Exception as e: return False, str(e)
    finally:
        if headers and "Session-Token" in headers: requests.get(f"{GLPI_URL}/killSession", headers=headers)
        conexao.close()

conexao = inicializar_banco()
st.title("💻 Sistema de Gestão - TI")

aba_inv, aba_est, aba_lic, aba_glpi, aba_import = st.tabs(["📦 Movimentações", "📊 Estoque Atual", "🔑 Licenças", "🌐 Visão Geral (GLPI)", "⚡ Importar GLPI"])

with aba_inv:
    st.header("Entradas e Saídas")
    with st.form("f_entrada"):
        st.subheader("Cadastro de Itens")
        c1, c2, c3 = st.columns(3)
        n = c1.text_input("Nome do Item").strip().lower()
        q = c2.number_input("Quantidade", min_value=1)
        m = c3.number_input("Alerta Mínimo", value=2)
        if st.form_submit_button("Salvar Entrada"):
            if n:
                cursor = conexao.cursor()
                cursor.execute("SELECT quantidade FROM itens WHERE nome=?", (n,))
                res = cursor.fetchone()
                if res: cursor.execute("UPDATE itens SET quantidade=quantidade+?, estoque_minimo=? WHERE nome=?", (q, m, n))
                else: cursor.execute("INSERT INTO itens (nome, quantidade, estoque_minimo) VALUES (?,?,?)", (n, q, m))
                conexao.commit(); st.success("Estoque atualizado!"); st.rerun()

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Registrar Retirada")
        it = pd.read_sql_query("SELECT nome FROM itens", conexao)
        if not it.empty:
            with st.form("f_saida"):
                sel = st.selectbox("Item", [x.title() for x in it['nome'].tolist()])
                qs = st.number_input("Qtd", min_value=1)
                cc = st.text_input("Centro de Custo")
                dest = st.text_input("Recebedor")
                if st.form_submit_button("Baixa"):
                    cursor = conexao.cursor()
                    cursor.execute("UPDATE itens SET quantidade=quantidade-? WHERE nome=?", (qs, sel.lower()))
                    cursor.execute("INSERT INTO fila_baixas (nome_item, quantidade, centro_custo, pessoa_recebeu) VALUES (?,?,?,?)", (sel.lower(), qs, cc, dest))
                    conexao.commit(); st.rerun()
    with col2:
        st.subheader("Pendentes")
        pnd = pd.read_sql_query("SELECT * FROM fila_baixas WHERE concluido=0", conexao)
        for _, r in pnd.iterrows():
            if st.checkbox(f"{r['quantidade']}x {r['nome_item'].title()} -> {r['pessoa_recebeu']}", key=f"b_{r['id']}"):
                cursor = conexao.cursor(); cursor.execute("UPDATE fila_baixas SET concluido=1 WHERE id=?", (r['id'],))
                conexao.commit(); st.rerun()

with aba_est:
    st.header("Estoque Atual")
    df_est = pd.read_sql_query("SELECT nome as Item, quantidade as Qtd, estoque_minimo as Alerta FROM itens", conexao)
    if not df_est.empty:
        df_est['Item'] = df_est['Item'].str.title()
        def colorir_estoque(row):
            return ['color: #ff4b4b; font-weight: bold' if row['Qtd'] <= row['Alerta'] else ''] * 3
        st.dataframe(df_est.style.apply(colorir_estoque, axis=1), use_container_width=True, hide_index=True)
        rem_i = st.selectbox("Excluir item permanentemente:", df_est['Item'].tolist())
        if st.button("Remover do Sistema", type="primary"):
            cursor = conexao.cursor(); cursor.execute("DELETE FROM itens WHERE nome=?", (rem_i.lower(),))
            conexao.commit(); st.rerun()
    else: st.info("O estoque está vazio.")

with aba_lic:
    st.header("Licenciamento")
    if st.session_state.msg_lic: st.info(st.session_state.msg_lic); st.session_state.msg_lic = None
    with st.form("f_cad_lic"):
        st.subheader("Cadastrar Licença")
        c1, c2 = st.columns(2); nl = c1.text_input("E-mail Licença"); cp = c2.number_input("Limite", min_value=1)
        if st.form_submit_button("Cadastrar"):
            cursor = conexao.cursor(); cursor.execute("INSERT INTO licencas (nome, capacidade_maxima) VALUES (?,?)", (nl, cp))
            conexao.commit(); st.rerun()
    lcs = pd.read_sql_query("SELECT * FROM licencas", conexao)
    if not lcs.empty:
        with st.form("f_vin"):
            st.subheader("Vincular Pessoa")
            d_l = dict(zip(lcs['nome'], lcs['id'])); sl = st.selectbox("Escolha a Licença", list(d_l.keys()))
            un = st.text_input("Nome no GLPI"); ue = st.text_input("E-mail Pessoa")
            if st.form_submit_button("Executar Vínculo"):
                note, ser, c_id = consultar_glpi_completo(un)
                cursor = conexao.cursor(); cursor.execute("INSERT INTO utilizadores (nome_pessoa, email_pessoa, id_licenca, notebook, serial, id_computador) VALUES (?,?,?,?,?,?)", (un, ue, d_l[sl], note, ser, c_id))
                conexao.commit(); st.rerun()
        st.divider()
        sl_v = st.selectbox("Gerenciar licença:", list(d_l.keys()))
        us = pd.read_sql_query("SELECT id, nome_pessoa as Nome FROM utilizadores WHERE id_licenca=(SELECT id FROM licencas WHERE nome=?)", conexao, params=(sl_v,))
        if not us.empty:
            st.table(us); u_rem = st.selectbox("Remover:", us['Nome'].tolist())
            if st.button("Remover Pessoa"):
                cursor = conexao.cursor(); cursor.execute("UPDATE utilizadores SET id_licenca=NULL WHERE nome_pessoa=?", (u_rem,))
                conexao.commit(); st.rerun()
        st.divider()
        lic_rem = st.selectbox("Apagar licença inteira:", lcs['nome'].tolist())
        if st.button("Apagar da Lista", type="primary"):
            id_l = int(lcs.loc[lcs['nome'] == lic_rem, 'id'].values[0])
            cursor = conexao.cursor(); cursor.execute("DELETE FROM utilizadores WHERE id_licenca=?", (id_l,)); cursor.execute("DELETE FROM licencas WHERE id=?", (id_l,))
            conexao.commit(); st.rerun()

with aba_glpi:
    st.header("🌐 Visão Geral")
    df_vg = pd.read_sql_query("SELECT u.nome_pessoa as Colaborador, u.email_pessoa as E_mail, l.nome as Licença, u.notebook as Notebook, u.serial as Serial FROM utilizadores u LEFT JOIN licencas l ON u.id_licenca = l.id", conexao)
    
    c1, c2, c3 = st.columns([1,1,2])
    c1.metric("Total de Registros", len(df_vg))
    
    if c2.button("🔄 Sincronizar Agora", use_container_width=True):
        with st.status("Atualizando dados do GLPI..."): 
            log=st.empty()
            importar_e_sincronizar_tudo(log)
        st.rerun()
        
    busc = c3.text_input("🔍 Pesquisar (Qualquer campo):")
    
    if not df_vg.empty:
        df_vg.fillna('-', inplace=True)
        if busc: df_vg = df_vg[df_vg.apply(lambda r: r.astype(str).str.contains(busc, case=False).any(), axis=1)]
        st.dataframe(df_vg, use_container_width=True, hide_index=True)
    else: st.warning("Sem dados. Use a aba Importar.")

with aba_import:
    st.header("Robô de Importação")
    st.write("Esse botão atualiza todas as informações integradas com o GLPI.")
    if st.button("🚀 INICIAR IMPORTAÇÃO COMPLETA", type="primary", use_container_width=True):
        with st.status("Varrendo inventário do GLPI...") as s:
            log = st.empty(); suc, msg = importar_e_sincronizar_tudo(log)
            if suc: s.update(label="Feito!", state="complete"); st.success(msg); st.balloons()
            else: st.error(msg)