import java.lang.annotation.Annotation;
import java.lang.reflect.Method;
import java.util.Arrays;

/** Reflection only: no Spring context, bean creation, server, database or network. */
public final class SkillhubClassLinkProbe {
    private static final String PROPERTY = "skillhub.auth.deerflow-session.enabled";
    private static final String CONDITIONAL = "org.springframework.boot.autoconfigure.condition.ConditionalOnProperty";
    private static void require(boolean value, String code) {
        if (!value) throw new IllegalStateException(code);
    }
    private static Annotation annotation(Annotation[] values, String type) {
        return Arrays.stream(values).filter(a -> a.annotationType().getName().equals(type)).findFirst().orElse(null);
    }
    private static Object value(Annotation item, String attribute) throws Exception {
        return item.annotationType().getMethod(attribute).invoke(item);
    }
    private static void condition(Annotation item, String havingValue, boolean missing) throws Exception {
        require(item != null, "missing_conditional_annotation");
        require(Arrays.equals((String[])value(item, "name"), new String[]{PROPERTY}), "conditional_property");
        require(value(item, "havingValue").equals(havingValue), "conditional_having_value");
        require(value(item, "matchIfMissing").equals(missing), "conditional_match_if_missing");
    }
    private static Method method(Class<?> owner, String name) {
        return Arrays.stream(owner.getDeclaredMethods()).filter(m -> m.getName().equals(name)).findFirst().orElseThrow();
    }
    public static void main(String[] args) throws Exception {
        require(args.length == 1 && (args[0].equals("candidate") || args[0].equals("baseline")), "mode");
        boolean candidate = args[0].equals("candidate");
        ClassLoader loader = Thread.currentThread().getContextClassLoader();
        // false means target classes are loaded without their static initializers.
        Class<?> compat = Class.forName("com.iflytek.skillhub.compat.ClawHubRegistrySecurityConfig", false, loader);
        Class<?> registry = Class.forName("com.iflytek.skillhub.integration.deerflow.DeerFlowRouteSecurityPolicyRegistry", false, loader);
        Class<?> base = Class.forName("com.iflytek.skillhub.auth.policy.RouteSecurityPolicyRegistry", false, loader);
        Class<?> policy = Class.forName("com.iflytek.skillhub.auth.policy.RouteSecurityPolicyRegistry$RouteAuthorizationPolicy", false, loader);
        Class<?> access = Class.forName("com.iflytek.skillhub.auth.policy.RouteSecurityPolicyRegistry$AccessLevel", false, loader);
        for (Class<?> type : new Class<?>[]{compat,registry,base,policy,access}) {
            type.getDeclaredMethods(); type.getDeclaredConstructors(); type.getDeclaredFields(); type.getDeclaredAnnotations();
        }
        Annotation compatCondition = annotation(compat.getDeclaredAnnotations(), CONDITIONAL);
        if (candidate) condition(compatCondition, "false", true);
        else require(compatCondition == null, "baseline_unexpected_condition");
        condition(annotation(registry.getDeclaredAnnotations(), CONDITIONAL), "true", false);
        Method label = method(compat, "publicLabelFilterChain");
        Method claw = method(compat, "clawHubRegistryFilterChain");
        require(value(annotation(label.getDeclaredAnnotations(), "org.springframework.core.annotation.Order"), "value").equals(0), "label_order");
        require(value(annotation(claw.getDeclaredAnnotations(), "org.springframework.core.annotation.Order"), "value").equals(1), "compat_order");
        require(base.getDeclaredMethod("authorizationPolicies").getReturnType().equals(java.util.List.class), "base_policy_signature");
        require(policy.getDeclaredMethod("accessLevel").getReturnType().equals(access), "access_level_signature");
        require(policy.getDeclaredMethod("pattern").getReturnType().equals(String.class), "pattern_signature");
        require(access.getDeclaredField("PERMIT_ALL").getType().equals(access), "permit_all_enum_field");
        boolean overrides = Arrays.stream(registry.getDeclaredMethods()).anyMatch(m -> m.getName().equals("authorizationPolicies"));
        require(overrides == candidate, "registry_override_marker");
        if (candidate) {
            require(registry.getDeclaredMethod("authorizationPolicies").getReturnType().equals(java.util.List.class), "override_signature");
            require(method(registry,"isRegistryDataPath").getReturnType().equals(boolean.class), "path_predicate_signature");
            require(method(policy,"authenticated").getReturnType().equals(policy), "authenticated_factory_signature");
        }
        System.out.println("{\"schema\":\"skillhub-java-class-link.v1\",\"status\":\"PASS\",\"mode\":\""+args[0]+"\",\"java_version\":\""+System.getProperty("java.version")+"\",\"os_arch\":\""+System.getProperty("os.arch")+"\",\"classes_linked\":5,\"class_initialization_requested\":false,\"patched_registry_override\":"+overrides+",\"public_chain_conditional_false_marker\":"+(compatCondition!=null)+",\"public_chain_orders\":[0,1],\"enterprise_registry_conditional_true_marker\":true,\"application_started\":false,\"spring_context_started\":false,\"database_access\":false,\"network_requests\":0}");
    }
}
