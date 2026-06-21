import pymysql
import re
from pymysql.constants import CLIENT

def fix_sql_dump(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        sql = f.read()

    pattern = r"ALTER TABLE `([^`]+)`\s+MODIFY `([^`]+)` ([^;]+AUTO_INCREMENT[^;]*);"
    matches = re.finditer(pattern, sql)
    
    modifications = {}
    for match in matches:
        table = match.group(1)
        col = match.group(2)
        definition = match.group(3)
        modifications[table] = (col, definition)
        
    sql = re.sub(pattern, "-- removed alter table modify", sql)
    
    for table, (col, definition) in modifications.items():
        create_pattern = r"(CREATE TABLE `"+table+r"` \([\s\S]*?`"+col+r"` )([^,]+)(,)"
        def replacer(m):
            clean_def = re.sub(r", AUTO_INCREMENT=\d+", "", definition)
            return m.group(1) + clean_def + m.group(3)
            
        sql = re.sub(create_pattern, replacer, sql)

    return sql

try:
    print("Fixing SQL dump for TiDB compatibility...")
    fixed_sql = fix_sql_dump(r"C:\Users\DELL\Downloads\sidiq18.sql")
    
    print("Connecting to TiDB...")
    conn = pymysql.connect(
        host='gateway01.ap-southeast-1.prod.alicloud.tidbcloud.com', 
        port=4000, 
        user='3AHWVHeNdXYTV9m.root', 
        password='o4qsEk8fUi8QBdh5', 
        database='test', 
        ssl_verify_cert=True, 
        ssl_verify_identity=True, 
        client_flag=CLIENT.MULTI_STATEMENTS
    )
    
    c = conn.cursor()
    
    print("Cleaning up target database...")
    c.execute("SHOW TABLES")
    tables = [row[0] for row in c.fetchall()]
    if tables:
        c.execute("SET FOREIGN_KEY_CHECKS = 0;")
        for t in tables:
            c.execute(f"DROP TABLE IF EXISTS `{t}`")
        c.execute("SET FOREIGN_KEY_CHECKS = 1;")
    
    print("Executing fixed SQL on TiDB (this may take a minute)...")
    c.execute(fixed_sql)
    conn.commit()
    print("SUCCESS: Data successfully imported to TiDB!")
except Exception as e:
    print("ERROR:", e)
