Source: https://foundry.mypaytm.com/onboarding/team-infra

# Team Infrastructure Onboarding 
One-time per team, done by your DevOps. To run Ark compute in your team's AWS account, two things must be wired: VPC peering and a cross-account IAM role. Your DevOps does both, and the platform team completes each.
If you are an engineer who just needs somewhere to run
You do not have to read this page. It is written for whoever administers your team's AWS account. Forward it to them with this ask, and carry on with [the rest of onboarding](/onboarding/):
> We need two things to run Foundry compute in our AWS account. First, raise a VPC peering request to the Ark account and send back the `pcx-` id. Second, open Foundry's **Connect** page, generate the cross-account role from the AWS card, apply it, and send back the role ARN and the ExternalId. The generated policy grants a couple of dozen EC2 and SSM actions and nothing else, and every destructive one is conditioned on a tag Foundry can only apply to resources it created. The guarantees are listed at the top of the generated file.
This is usually the longest-lead item in onboarding, so it is worth starting on your first day even though you cannot finish it yourself.
## VPC peering 
**Ark side (same for every team - accepter):**
    
     Account:  880170353725 (pai-risk-mlops)
    VPC:      vpc-0ff84c1892baaab28
    CIDR:     10.72.216.0/23
**Your side (per team - requester).** Find in AWS console → VPC → Your VPCs; account id via `aws sts get-caller-identity`:
    
    Account:  <your AWS account id>
    VPC:      <your VPC id>
    CIDR:     <your VPC CIDR>   ← must NOT overlap 10.72.216.0/23 or another team's CIDR
**Steps:**
  1. From your account: VPC → Peering connections → Create (requester = your VPC, accepter = account `880170353725` / `vpc-0ff84c1892baaab28`). Share the `pcx-` id with the platform team.
  2. Platform team accepts and adds the return route on the Ark side.
  3. Your side: route `10.72.216.0/23 → pcx-<id>` in every route table your compute subnets use; security groups: outbound 443 to `10.72.216.0/23`, inbound `19300` from it.
  4. Verify from any instance in your VPC:

bash
    
    curl -s -m 5 https://ark.internal.ap-south-1.platform.mlops.pai.mypaytm.com/api/health
Any HTTP response means routing works.
**Reference (existing wiring):** PML MF - `pcx-09bd4bd7ea052ec57`, requester `vpc-0551226b7a30ce96c` (`10.10.0.0/16`), account `656952484900`.
## Cross-account IAM role 
Ark's control plane (`arn:aws:iam::880170353725:role/platform-ark`) assumes a role in _your_ account to provision or adopt EC2.
**Generate the role from the Connect page rather than writing one by hand.** Sign in to Ark, open **Connect** , and the AWS onboarding card emits a ready-to-run AWS CLI script or Terraform snippet, filled in with your ExternalId and the control plane's real principal ARN. Pick `provision` if Ark should launch instances for you, `adopt` if you stand up the boxes yourself.
### What the generated role can and cannot do 
The whole policy is built around one ownership tag, `ark:managed=true`:
  - Ark can only terminate, stop, reboot or delete resources carrying that tag, so it cannot touch instances or security groups it did not create.
  - Ark cannot apply that tag to anything it did not create. Tagging is permitted only as part of `RunInstances` / `CreateSecurityGroup` (`ec2:CreateAction`), and an explicit `Deny` blocks every other path. Without this the first guarantee would be worthless: a role that can tag your instance can then terminate it.
  - SSM reach is limited to tagged instances, three named documents, and sessions Ark itself opened, so it cannot terminate an engineer's Session Manager session.
  - In `adopt` mode Ark gets SSM reach and `StartInstances` and nothing else. Terminate and stop are denied unconditionally, because you own that box's lifecycle. You apply the tag yourself to make the box reachable, and an explicit `Deny` makes sure that does not double as permission to destroy it.
  - With EKS enabled, the AWS-side grant is read-only (`eks:DescribeCluster` on one named cluster) and k8s access is an access entry scoped to one namespace via `AmazonEKSEditPolicy`. Never cluster-admin.

Everything else, including VPCs, subnets, route tables, gateways, peerings, NACLs, ENIs and IAM, is absent from the Allow set. IAM is default-deny, so absence is the control. **Read the Allow statements: they are the whole of what this role can do.** The policy deliberately does not carry a long list of `Deny` statements for things it never granted, because such a list can never keep pace with the actions AWS adds and reads as a guarantee it cannot make.
Every generated file lists these guarantees in its header, naming the statement that enforces each, so whoever reviews it does not have to derive them from the JSON.
### Optional: compute cloning 
`ark compute create --clone-from` snapshots a box into an AMI, and that needs `ec2:CreateImage` plus the ability to release the image afterwards. It is off by default, because an AMI holds EBS snapshots that bill until deleted and that should be a decision, not a surprise. Tick **Allow compute cloning** on the Connect page if you want it. The release grants (`DeregisterImage`, `DeleteSnapshot`) are restricted to images Ark itself created, on the same ownership tag as everything else.
Cost reporting is deliberately not offered. Ark can show per-compute spend when it has `ce:GetCostAndUsage`, but Cost Explorer supports no resource-level permissions and no tag conditions, so the narrowest possible grant is still account-wide read of your billing data. That is too much to ask for a number in a UI, so the generated role never includes it and the spend figure stays blank.
### Migrating an existing tenant off an admin role 
If Ark already provisioned compute in your account under a broad role, those instances and security groups carry only the older `Component=ark` label, not `ark:managed=true`. The tightened role keys on `ark:managed`, so it will refuse to terminate them. That is fail-safe rather than dangerous, but it strands resources, and the policy deliberately forbids Ark from applying the tag itself.
Backfill the tag once, with your own credentials, **before** switching the compute rows to the new role:
bash
    
    P=<your-aws-profile>; R=<region>
    
    # Instances Ark provisioned under the old label.
    IDS=$(aws ec2 describe-instances --profile "$P" --region "$R" \
      --filters "Name=tag:Component,Values=ark" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --query 'Reservations[].Instances[].InstanceId' --output text)
    [ -n "$IDS" ] && aws ec2 create-tags --profile "$P" --region "$R" \
      --resources $IDS --tags Key=ark:managed,Value=true
    
    # Security groups Ark created for them.
    SGS=$(aws ec2 describe-security-groups --profile "$P" --region "$R" \
      --filters "Name=tag:Component,Values=ark" \
      --query 'SecurityGroups[].GroupId' --output text)
    [ -n "$SGS" ] && aws ec2 create-tags --profile "$P" --region "$R" \
      --resources $SGS --tags Key=ark:managed,Value=true
Check what it matched before you trust it. Anything in your account tagged `Component=ark` that Ark did **not** create would be swept in, and tagging it hands Ark the ability to terminate it:
bash
    
    aws ec2 describe-instances --profile "$P" --region "$R" \
      --filters "Name=tag:Component,Values=ark" \
      --query 'Reservations[].Instances[].[InstanceId,Tags[?Key==`Name`].Value|[0]]' --output table
Instances created after the switch are born tagged, so this is a one-time step per region.
Want a hard ceiling?
The role's permissions can still be widened by anyone in your account who attaches another policy to it. If you want a limit that survives that, apply a **Service Control Policy** in your own AWS Organization. That is the mechanism designed for it, and no set of `Deny` statements inside this role substitutes for one.
Only if you reuse an existing role
The generated artifact creates a **dedicated** role, which is what we recommend. If you instead append the Ark trust statement to an existing admin/SAML role, note that `update-assume-role-policy` **replaces the entire trust document** \- fetch the existing doc, append, and put it back. Writing it from scratch wipes your SAML statement and locks your own team out of the role. A reused admin role also discards every guarantee above, since the role keeps whatever permissions it already had.
Send the platform team the **role ARN** and they register it, with the ExternalId, on your tenant's compute config.
## Summary of the ask to your DevOps 
> "Raise the peering request (send us the pcx id), and give us the ARN of the cross-account role generated from Ark's Connect page (ExternalId `ark-cross-account-<slug>`). The generated policy grants about two dozen EC2/SSM actions and nothing else, and every destructive one is conditioned on a tag Ark can only set on resources it creates. The guarantees are listed at the top of the file."
