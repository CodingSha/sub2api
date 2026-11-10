-- 由数据库管理员在已核实的目标库执行；本文件不设置密码，不允许Worker迁移或发布。
-- 两个角色首次创建后，通过部署凭证管理分别配置登录密码。
BEGIN;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='zhiyongli_worker') THEN
    CREATE ROLE zhiyongli_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='zhiyongli_source') THEN
    CREATE ROLE zhiyongli_source LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('zhiyongli_worker','zhiyongli_source')
      AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION '现有同名角色权限过大，停止授权，请先人工核对';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
      WHERE r.rolname IN ('zhiyongli_worker','zhiyongli_source')) THEN
    RAISE EXCEPTION '同名角色已有其他角色成员关系，停止授权，请先核对SET ROLE边界';
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO zhiyongli_worker, zhiyongli_source;
GRANT SELECT ON zhiyongli_jobs TO zhiyongli_worker;
GRANT SELECT (id,email) ON users TO zhiyongli_worker;
GRANT UPDATE (state,phase,completed_units,total_units,message,updated_at) ON zhiyongli_jobs TO zhiyongli_worker;
GRANT SELECT (id,email,deleted_at) ON users TO zhiyongli_source;
GRANT SELECT (user_id) ON audit_whitelist_users TO zhiyongli_source;
GRANT SELECT (id,request_id,session_id,request_count,user_id,api_key_id,platform,endpoint,method,model,status_code,
  request_body,response_body,request_truncated,response_truncated,created_at,updated_at) ON llm_audit_logs TO zhiyongli_source;
ALTER ROLE zhiyongli_source SET default_transaction_read_only=on;
DO $$ BEGIN
  IF has_any_column_privilege('zhiyongli_worker','public.zhiyongli_reports','INSERT')
      OR has_any_column_privilege('zhiyongli_worker','public.zhiyongli_reports','UPDATE')
      OR has_table_privilege('zhiyongli_worker','public.zhiyongli_reports','DELETE,TRUNCATE')
      OR has_any_column_privilege('zhiyongli_worker','public.api_keys','SELECT')
      OR has_any_column_privilege('zhiyongli_source','public.api_keys','SELECT')
      OR has_any_column_privilege('zhiyongli_source','public.llm_audit_logs','UPDATE')
      OR has_table_privilege('zhiyongli_source','public.llm_audit_logs','DELETE,TRUNCATE') THEN
    RAISE EXCEPTION '已有授权或PUBLIC权限超出Worker边界，整次授权回滚；请人工核对';
  END IF;
END $$;
COMMIT;
